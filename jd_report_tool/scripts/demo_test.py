from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.environment import module_installed

_missing = [m for m in ["pandas", "openpyxl"] if not module_installed(m)]
if _missing:
    print("缺少 Demo 测试依赖：" + ", ".join(_missing))
    print("请先运行 install.bat，或执行 pip install -r requirements.txt 后再运行本测试。")
    raise SystemExit(2)

import pandas as pd

from src.database import Database
from src.db_importer import BaseInfoImporter, sync_summary_to_db
from src.excel_processor import build_summary, read_excel_flexible
from src.exporter import OUTPUT_COLUMNS, SKU_COLUMNS, SPU_COLUMNS, export_summary

SAMPLE_RULES = {
    "reference": ("SPU", "SKU", "汇总"),
    "rank": ("搜索分析", "排名定位"),
    "sku": ("sku14975660", "商品明细"),
    "spu": ("spu-14975660", "商品明细"),
    "base": ("POP-SKU信息",),
}

SAMPLE_LABELS = {
    "reference": "SPU与SKU汇总",
    "rank": "搜索排名",
    "sku": "SKU商品明细",
    "spu": "SPU商品明细",
    "base": "POP-SKU信息",
}

EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


def list_excel_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in EXCEL_SUFFIXES],
        key=lambda path: path.name.lower(),
    )


def match_sample_files(folder: Path) -> tuple[dict[str, Path], dict[str, list[Path]], list[Path]]:
    excel_files = list_excel_files(folder)
    matches: dict[str, list[Path]] = {}
    for key, keywords in SAMPLE_RULES.items():
        lowered_keywords = [keyword.lower() for keyword in keywords]
        matches[key] = [
            path
            for path in excel_files
            if all(keyword in path.name.lower() for keyword in lowered_keywords)
        ]
    return {key: paths[0] for key, paths in matches.items() if paths}, matches, excel_files


def format_excel_listing(folder: Path, excel_files: list[Path]) -> str:
    if not folder.exists():
        return f"{folder}: 目录不存在"
    if not excel_files:
        return f"{folder}: 未找到 Excel 文件"
    names = "\n".join(f"  - {path.name}" for path in excel_files)
    return f"{folder}:\n{names}"


def resolve_sample_files() -> dict[str, Path]:
    """Find real Excel samples by filename keywords.

    Codex PRs must not commit Excel binaries. Users should manually place the
    five real workbooks in a samples directory.
    """
    search_dirs = [ROOT / "samples", REPO_ROOT / "samples"]
    if REPO_ROOT not in search_dirs:
        search_dirs.append(REPO_ROOT)

    reports = []
    for folder in search_dirs:
        files, matches, excel_files = match_sample_files(folder)
        if all(key in files for key in SAMPLE_RULES):
            print(f"使用真实样例目录: {folder}")
            return files

        missing = [SAMPLE_LABELS[key] for key in SAMPLE_RULES if not matches[key]]
        reports.append(f"{folder}: 缺失 {', '.join(missing)}")

    listings = []
    for folder in search_dirs:
        listings.append(format_excel_listing(folder, list_excel_files(folder)))
    raise FileNotFoundError(
        "未找到完整真实 Excel 样例。请确认 samples 目录下有 5 个真实 Excel 文件。\n"
        + "\n".join(reports)
        + "\n\nsamples 目录下实际 Excel 文件:\n"
        + "\n".join(listings)
    )


def reference_columns(reference_file: Path) -> tuple[list[str], list[str]]:
    try:
        xls = pd.ExcelFile(reference_file, engine="openpyxl")
        if {"SPU数据汇总", "SKU数据汇总"}.issubset(set(xls.sheet_names)):
            spu_cols = list(pd.read_excel(reference_file, sheet_name="SPU数据汇总", nrows=0, engine="openpyxl").columns)
            sku_cols = list(pd.read_excel(reference_file, sheet_name="SKU数据汇总", nrows=0, engine="openpyxl").columns)
            return [str(c).strip() for c in spu_cols], [str(c).strip() for c in sku_cols]
    except Exception as exc:
        print(f"读取参考表工作表失败，改用标准字段: {exc}")
    return SPU_COLUMNS, SKU_COLUMNS


def list_unmatched(sku_df: pd.DataFrame) -> list[str]:
    if sku_df.empty:
        return []
    mask = sku_df["sku"].notna() & ((sku_df["spu"].isna()) | (sku_df["spu"].astype(str).str.strip() == "") |
                                   (sku_df["货号"].isna()) | (sku_df["货号"].astype(str).str.strip() == ""))
    return sorted(sku_df.loc[mask, "sku"].astype(str).unique().tolist())


def main() -> None:
    files = resolve_sample_files()

    print("读取文件:")
    for key, path in files.items():
        print(f"  {key}: {path}")

    # 显式读取 4 个源文件 + 参考表，确保字段识别阶段可暴露问题。
    for key in ["spu", "sku", "rank", "base", "reference"]:
        df = read_excel_flexible(files[key])
        print(f"  {key} rows={len(df)} cols={list(df.columns)[:12]}")

    db_path = ROOT / "data" / "database" / "jd_operation.db"
    if db_path.exists():
        db_path.unlink()
    db = Database(db_path)
    db.initialize()
    import_result = BaseInfoImporter(db).import_base_info(1, files["base"])

    date_start = "2026-06-01"
    date_end = "2026-06-09"
    spu_df, sku_df = build_summary(files["spu"], files["sku"], files["rank"], 1, db, files["base"], date_start)

    ref_spu_cols, ref_sku_cols = reference_columns(files["reference"])
    expected_spu = ref_spu_cols or SPU_COLUMNS
    expected_sku = ref_sku_cols or SKU_COLUMNS
    if list(spu_df.columns) != expected_spu:
        raise AssertionError(f"SPU 表头不正确: actual={list(spu_df.columns)} expected={expected_spu}")
    if list(sku_df.columns) != expected_sku:
        raise AssertionError(f"SKU 表头不正确: actual={list(sku_df.columns)} expected={expected_sku}")
    if spu_df.empty or sku_df.empty:
        raise AssertionError("汇总结果为空")

    unmatched = list_unmatched(sku_df)
    if len(unmatched) == len(sku_df):
        raise AssertionError("所有 SKU 都未匹配到 SPU/货号，请检查基础信息字段识别")
    if sku_df["类目排名"].notna().sum() == 0:
        raise AssertionError("商品排名定位表未按 SKU + 日期补充任何类目排名")

    output_dir = ROOT / "data" / "output"
    output = output_dir / "艺颂_SPU与SKU数据汇总_2026-06-01_2026-06-09.xlsx"
    if output.exists():
        output.unlink()
    output = export_summary(spu_df, sku_df, "艺颂", "2026-06-01", "2026-06-09", output_dir)
    if output.name != "艺颂_SPU与SKU数据汇总_2026-06-01_2026-06-09.xlsx":
        raise AssertionError(f"输出文件名不符合要求: {output.name}")

    xls = pd.ExcelFile(output, engine="openpyxl")
    if set(xls.sheet_names) != {"SPU数据汇总", "SKU数据汇总", "输出表"}:
        raise AssertionError(f"输出工作表不正确: {xls.sheet_names}")
    out_spu_cols = list(pd.read_excel(output, sheet_name="SPU数据汇总", nrows=0, engine="openpyxl").columns)
    out_sku_cols = list(pd.read_excel(output, sheet_name="SKU数据汇总", nrows=0, engine="openpyxl").columns)
    out_output_cols = list(pd.read_excel(output, sheet_name="输出表", nrows=0, engine="openpyxl").columns)
    if out_spu_cols != expected_spu or out_sku_cols != expected_sku:
        raise AssertionError("输出 Excel 字段顺序与参考表不一致")
    if out_output_cols != OUTPUT_COLUMNS:
        raise AssertionError("输出表字段顺序不正确")

    sync_result = sync_summary_to_db(1, spu_df, sku_df, db)
    daily_spu_count = db.query("SELECT COUNT(*) AS c FROM daily_spu_data")[0]["c"]
    daily_sku_count = db.query("SELECT COUNT(*) AS c FROM daily_sku_data")[0]["c"]

    print("import_result=", import_result)
    print("output=", output)
    print("spu_rows=", len(spu_df))
    print("sku_rows=", len(sku_df))
    print("daily_spu_rows=", daily_spu_count)
    print("daily_sku_rows=", daily_sku_count)
    print("rank_filled_rows=", int(sku_df["类目排名"].notna().sum()))
    print("unmatched_sku_count=", len(unmatched))
    print("unmatched_skus=", unmatched[:200])
    print("sync_result=", sync_result)
    print("using_real_samples=", True)
    print("demo ok")


if __name__ == "__main__":
    main()
