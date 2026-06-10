from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from .logger import logger
from .utils import clean_column_name, normalize_date, ratio, safe_text, to_int, to_number

ID_HINTS = [
    "spu", "sku", "skuid", "sku编码", "sku编号", "商品编码", "商品编号", "商品id", "商品ID",
    "货号", "商家编码", "商家商品编码", "款号", "外部编码",
]
DATE_KEYS = ["日期", "时间", "报表日期", "统计日期", "数据日期"]
SPU_KEYS = ["商品编码", "商品编号", "商品id", "商品ID", "spu", "SPU"]
SKU_KEYS = ["SKU编码", "sku编码", "SKU编号", "sku编号", "skuid", "skuId", "SKU", "sku"]
ITEM_NO_KEYS = ["货号", "商品货号", "款号", "商家编码", "商家商品编码", "外部编码", "外部货号"]


def _flatten_columns(columns: Any) -> list[str]:
    result: list[str] = []
    for col in columns:
        if isinstance(col, tuple):
            parts = [clean_column_name(x) for x in col if clean_column_name(x) and not str(x).startswith("Unnamed")]
            name = "_".join(dict.fromkeys(parts))
        else:
            name = clean_column_name(col)
        result.append(name)
    return result


def _score_columns(columns: list[str]) -> int:
    score = 0
    for col in columns:
        name = clean_column_name(col).lower()
        score += sum(1 for h in ID_HINTS if h.lower() in name) * 3
        score += sum(1 for k in ["时间", "日期", "成交", "曝光", "点击", "访客", "浏览", "排名", "金额", "件数"] if k in name)
        if name.startswith("unnamed") or not name:
            score -= 2
    return score


def _promote_scanned_header(raw: pd.DataFrame, header_row: int) -> pd.DataFrame:
    header_values = [clean_column_name(v) for v in raw.iloc[header_row].tolist()]
    df = raw.iloc[header_row + 1:].copy()
    df.columns = header_values
    df = df.loc[:, [c for c in df.columns if c and not str(c).startswith("Unnamed")]]
    return df


def read_excel_flexible(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    logger.info("读取 Excel: %s", path)
    best: pd.DataFrame | None = None
    best_score = -999
    # 先尝试常见单层/双层表头。
    for header in [0, 1, 2, 3, 4, 5, [0, 1], [1, 2], [2, 3], [3, 4]]:
        try:
            df = pd.read_excel(path, dtype=str, header=header, engine="openpyxl")
            df.columns = _flatten_columns(df.columns)
            df = df.dropna(how="all").copy()
            df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]
            score = _score_columns(list(df.columns))
            if score > best_score and len(df.columns) > 1:
                best, best_score = df, score
        except Exception:
            continue
    # 真实商智导出有时前面带标题/说明行，扫描前 30 行自动找表头。
    try:
        raw = pd.read_excel(path, dtype=str, header=None, engine="openpyxl")
        max_scan = min(30, len(raw))
        for row_idx in range(max_scan):
            header_values = [clean_column_name(v) for v in raw.iloc[row_idx].tolist()]
            score = _score_columns(header_values)
            non_empty = sum(1 for v in header_values if v)
            if non_empty > 1 and score > best_score:
                best = _promote_scanned_header(raw, row_idx)
                best_score = score
    except Exception as exc:
        logger.warning("扫描表头失败: %s", exc)
    if best is None:
        best = pd.read_excel(path, dtype=str, engine="openpyxl")
        best.columns = _flatten_columns(best.columns)
    best = best.dropna(how="all").copy()
    best = best[~best.apply(lambda r: any(safe_text(v) in {"合计", "总计", "汇总"} for v in r.values), axis=1)]
    best.columns = [clean_column_name(c) for c in best.columns]
    logger.info("Excel 表头识别完成: file=%s score=%s columns=%s", path.name, best_score, list(best.columns))
    return best


def find_col(df: pd.DataFrame, include: list[str], exclude: list[str] | None = None) -> str | None:
    exclude = exclude or []
    cols = list(df.columns)
    for col in cols:
        name = clean_column_name(col).lower()
        if all(k.lower() in name for k in include) and not any(x.lower() in name for x in exclude):
            return col
    for col in cols:
        name = clean_column_name(col).lower()
        if any(k.lower() in name for k in include) and not any(x.lower() in name for x in exclude):
            return col
    return None


def find_col_any(df: pd.DataFrame, candidates: list[str], exclude: list[str] | None = None) -> str | None:
    exclude = exclude or []
    normalized = [(col, clean_column_name(col).lower()) for col in df.columns]
    for candidate in candidates:
        key = clean_column_name(candidate).lower()
        for col, name in normalized:
            if name == key and not any(x.lower() in name for x in exclude):
                return col
    for candidate in candidates:
        key = clean_column_name(candidate).lower()
        for col, name in normalized:
            if key and key in name and not any(x.lower() in name for x in exclude):
                return col
    return None


def extract_date_from_filename(path: str | Path) -> str | None:
    text = Path(path).name
    dates = re.findall(r"20\d{2}[-_\.]\d{1,2}[-_\.]\d{1,2}", text)
    if dates:
        return normalize_date(dates[-1])
    return None


def normalize_report_date(df: pd.DataFrame, path: str | Path, date_start: str | None = None) -> pd.Series:
    col = find_col_any(df, DATE_KEYS)
    if col:
        s = df[col].apply(lambda x: normalize_date(x, extract_date_from_filename(path) or date_start))
    else:
        default = extract_date_from_filename(path) or date_start
        logger.warning("报表缺少日期/时间字段，使用默认日期: %s", default)
        s = pd.Series([default] * len(df), index=df.index)
    return s


def build_base_mappings(base_file: str | Path | None) -> tuple[dict[str, str], dict[str, dict[str, str]], pd.DataFrame]:
    if not base_file:
        return {}, {}, pd.DataFrame()
    df = read_excel_flexible(base_file)
    sku_col = find_col_any(df, SKU_KEYS)
    spu_col = find_col_any(df, SPU_KEYS)
    item_col = find_col_any(df, ITEM_NO_KEYS)
    spu_map: dict[str, str] = {}
    sku_map: dict[str, dict[str, str]] = {}
    for _, row in df.iterrows():
        sku = safe_text(row.get(sku_col)) if sku_col else ""
        spu = safe_text(row.get(spu_col)) if spu_col else ""
        item_no = safe_text(row.get(item_col)) if item_col else ""
        if spu and item_no:
            spu_map[spu] = item_no
        if sku:
            sku_map[sku] = {"spu": spu, "item_no": item_no}
    logger.info("基础信息映射读取完成: SPU=%s SKU=%s", len(spu_map), len(sku_map))
    return spu_map, sku_map, df


def normalize_product_detail(path: str | Path, level: str, date_start: str | None = None) -> pd.DataFrame:
    df = read_excel_flexible(path)
    date_series = normalize_report_date(df, path, date_start)
    id_col = None
    if level.upper() == "SPU":
        id_col = find_col_any(df, SPU_KEYS)
    else:
        id_col = find_col_any(df, SKU_KEYS)
    if not id_col:
        raise ValueError(f"{level} 报表找不到 {'SPU/商品编码' if level.upper()=='SPU' else 'SKU编码'} 字段")

    exposure_col = find_col_any(df, ["曝光次数", "曝光量", "曝光人数", "搜索曝光", "曝光"])
    if not exposure_col:
        exposure_col = find_col_any(df, ["商品浏览量", "浏览量", "浏览次数", "商品浏览次数"])
        logger.warning("字段缺失: 未找到曝光字段，使用商品浏览量/浏览量代替曝光次数: %s", exposure_col)
    click_col = find_col_any(df, ["点击次数", "点击量", "搜索点击", "点击"])
    if not click_col:
        click_col = find_col_any(df, ["商品访客数", "访客数", "访客人数", "商品访客人数"])
        logger.warning("字段缺失: 未找到点击字段，使用商品访客数/访客数代替点击次数: %s", click_col)
    qty_col = find_col_any(df, ["成交商品件数", "成交件数", "成交件数合计", "下单件数", "成交商品数量"])
    gmv_col = find_col_any(df, ["成交金额", "成交额", "销售额", "成交金额合计", "成交金额(元)"])

    out = pd.DataFrame()
    out["时间"] = date_series
    if level.upper() == "SPU":
        out["spu"] = df[id_col].apply(safe_text)
    else:
        out["sku"] = df[id_col].apply(safe_text)
    out["曝光次数"] = df[exposure_col].apply(to_int) if exposure_col else None
    out["点击次数"] = df[click_col].apply(to_int) if click_col else None
    out["成交商品件数"] = df[qty_col].apply(to_int) if qty_col else None
    out["成交金额"] = df[gmv_col].apply(to_number) if gmv_col else None
    out["曝光点击率"] = [ratio(c, e) for c, e in zip(out["点击次数"], out["曝光次数"])]
    out["曝光转化率"] = [ratio(q, e) for q, e in zip(out["成交商品件数"], out["曝光次数"])]
    id_name = "spu" if level.upper() == "SPU" else "sku"
    out = out[out[id_name].astype(str).str.strip() != ""].copy()
    return out


def normalize_rank_location(path: str | Path, date_start: str | None = None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame(columns=["时间", "sku", "类目排名"])
    df = read_excel_flexible(path)
    sku_col = find_col_any(df, SKU_KEYS)
    rank_col = find_col_any(df, ["类目排名", "排名", "当前排名", "商品排名", "搜索排名"])
    if not sku_col or not rank_col:
        logger.warning("商品排名定位表缺少 SKU 或类目排名字段: sku_col=%s rank_col=%s", sku_col, rank_col)
        return pd.DataFrame(columns=["时间", "sku", "类目排名"])
    out = pd.DataFrame()
    out["时间"] = normalize_report_date(df, path, date_start)
    out["sku"] = df[sku_col].apply(safe_text)
    out["类目排名"] = df[rank_col].apply(to_int)
    out = out[(out["sku"] != "") & out["时间"].notna()].drop_duplicates(["时间", "sku"], keep="last")
    return out


def build_summary(spu_file: str | Path, sku_file: str | Path, rank_file: str | Path | None,
                  store_id: int, db=None, base_file: str | Path | None = None,
                  date_start: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    from .mapper import ProductMapper

    spu_df = normalize_product_detail(spu_file, "SPU", date_start)
    sku_df = normalize_product_detail(sku_file, "SKU", date_start)
    mapper = ProductMapper(store_id=store_id, db=db, base_file=base_file)
    spu_df = mapper.add_spu_item(spu_df)
    sku_df = mapper.add_sku_mapping(sku_df)
    rank_df = normalize_rank_location(rank_file, date_start) if rank_file else pd.DataFrame(columns=["时间", "sku", "类目排名"])
    if not rank_df.empty:
        sku_df = sku_df.merge(rank_df[["时间", "sku", "类目排名"]], on=["时间", "sku"], how="left")
    else:
        sku_df["类目排名"] = None
    spu_cols = ["时间", "spu", "货号", "曝光次数", "点击次数", "曝光点击率", "成交商品件数", "曝光转化率", "成交金额"]
    sku_cols = ["时间", "spu", "货号", "sku", "曝光次数", "点击次数", "曝光点击率", "成交商品件数", "曝光转化率", "成交金额", "类目排名"]
    return spu_df.reindex(columns=spu_cols), sku_df.reindex(columns=sku_cols)
