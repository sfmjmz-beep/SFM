from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .database import Database
from .db_models import REPORT_TYPES
from .excel_processor import (
    ITEM_NO_KEYS,
    SKU_KEYS,
    SPU_KEYS,
    extract_date_from_filename,
    find_col_any,
    normalize_report_date,
    read_excel_flexible,
)
from .logger import logger
from .utils import clean_column_name, normalize_date, safe_text, to_int, to_number, to_percent


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _first(df: pd.DataFrame, names: list[str]) -> str | None:
    return find_col_any(df, names)


def _val(row: pd.Series, col: str | None) -> Any:
    return row.get(col) if col else None


def _json_row(row: pd.Series) -> str:
    data = {str(k): safe_text(v) for k, v in row.to_dict().items() if safe_text(v)}
    return json.dumps(data, ensure_ascii=False)


def read_aftersale_excel(path: str | Path) -> pd.DataFrame:
    raw = pd.read_excel(path, dtype=str, header=None, engine="openpyxl").dropna(how="all")
    if len(raw) < 2:
        return read_excel_flexible(path)
    group = [clean_column_name(v) for v in raw.iloc[0].tolist()]
    header = [clean_column_name(v) for v in raw.iloc[1].tolist()]
    columns: list[str] = []
    for i, name in enumerate(header):
        if name:
            columns.append(name)
        elif i < len(group) and group[i]:
            columns.append(group[i])
        else:
            columns.append(f"列{i + 1}")
    df = raw.iloc[2:].copy()
    df.columns = columns
    df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]
    return df.dropna(how="all").copy()


class JDDataImporter:
    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def import_file(
        self,
        store_id: int,
        file_path: str | Path,
        report_type: str,
        date_start: str | None = None,
        date_end: str | None = None,
    ) -> dict[str, int | str]:
        if report_type not in REPORT_TYPES:
            raise ValueError(f"不支持的 report_type: {report_type}")
        self.db.initialize()
        path = Path(file_path)
        started = _now()
        total = success = failed = skipped = duplicate = unmatched = 0
        status = "success"
        error_message = ""
        try:
            if report_type == "product_master_sku":
                result = self.import_product_master(store_id, path, log=False)
            elif report_type == "product_detail_sku_daily":
                result = self.import_sku_daily(store_id, path, date_start, log=False)
            elif report_type == "product_detail_spu_daily":
                result = self.import_spu_daily(store_id, path, date_start, log=False)
            elif report_type == "search_rank_sku_daily":
                result = self.import_search_rank(store_id, path, date_start, log=False)
            elif report_type == "order_paid_items":
                result = self.import_order_items(store_id, path, log=False)
            elif report_type == "order_cancel_items":
                result = self.import_cancel_order_items(store_id, path, log=False)
            elif report_type == "order_refund_items":
                result = self.import_refund_items(store_id, path, log=False)
            elif report_type == "aftersale_services":
                result = self.import_aftersale_services(store_id, path, log=False)
            else:
                result = {"success_rows": 0, "total_rows": 0, "skipped_rows": 0, "duplicate_rows": 0, "unmatched_sku_count": 0}
            total = int(result.get("total_rows", 0))
            success = int(result.get("success_rows", 0))
            skipped = int(result.get("skipped_rows", 0))
            duplicate = int(result.get("duplicate_rows", 0))
            unmatched = int(result.get("unmatched_sku_count", 0))
        except Exception as exc:
            status = "failed"
            error_message = str(exc)
            failed = max(total - success - skipped, 1)
            logger.exception("导入失败: %s", exc)
        finally:
            date_start = date_start or extract_date_from_filename(path)
            date_end = date_end or date_start
            self._write_source_file(store_id, path, report_type, date_start, date_end)
            self._write_import_log(
                store_id, report_type, path, date_start, date_end, total, success,
                failed, skipped, duplicate, unmatched, status, error_message, started,
            )
        if status == "failed":
            raise ValueError(error_message)
        return {
            "report_type": report_type,
            "total_rows": total,
            "success_rows": success,
            "skipped_rows": skipped,
            "duplicate_rows": duplicate,
            "unmatched_sku_count": unmatched,
            "status": status,
        }

    def import_product_master(self, store_id: int, file_path: str | Path, log: bool = True) -> dict[str, int]:
        df = read_excel_flexible(file_path)
        sku_col = _first(df, ["SKUID", *SKU_KEYS])
        spu_col = _first(df, ["商品编码", *SPU_KEYS])
        product_code_col = _first(df, ["货号", *ITEM_NO_KEYS])
        sku_name_col = _first(df, ["商品名称", "SKU名称", "SKU名称", "标题", "商品标题"])
        merchant_sku_col = _first(df, ["商家SKU", "商家SKU编码"])
        sale_attr_col = _first(df, ["销售属性", "规格属性", "SKU规格"])
        c1_col = _first(df, ["一级类目"])
        c2_col = _first(df, ["二级类目"])
        c3_col = _first(df, ["三级类目", "叶子类目", "末级类目"])
        brand_col = _first(df, ["品牌", "品牌名称"])
        price_col = _first(df, ["京东价", "价格", "销售价"])
        stock_col = _first(df, ["库存", "可售库存"])
        status_col = _first(df, ["商品状态", "状态", "SKU状态"])
        url_col = _first(df, ["商品链接", "链接", "URL"])
        short_col = _first(df, ["短标题"])
        if not sku_col or not spu_col:
            raise ValueError("商品基础信息表至少需要 SKUID 和 商品编码 字段")

        now = _now()
        total = success = skipped = duplicate = 0
        spu_seen: set[str] = set()
        with self.db.connect() as conn:
            for _, row in df.iterrows():
                total += 1
                sku = safe_text(_val(row, sku_col))
                spu = safe_text(_val(row, spu_col))
                if not sku and not spu:
                    skipped += 1
                    continue
                product_code = safe_text(_val(row, product_code_col))
                sku_name = safe_text(_val(row, sku_name_col))
                if spu and spu not in spu_seen:
                    duplicate += self._exists(conn, "dim_spu", "store_id=? AND spu=?", (store_id, spu))
                    conn.execute(
                        """
                        INSERT INTO dim_spu(store_id, spu, spu_name, product_code, category_level1,
                            category_level2, category_level3, brand, status, main_sku, created_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(store_id, spu) DO UPDATE SET
                            spu_name=excluded.spu_name, product_code=excluded.product_code,
                            category_level1=excluded.category_level1, category_level2=excluded.category_level2,
                            category_level3=excluded.category_level3, brand=excluded.brand,
                            status=excluded.status, main_sku=excluded.main_sku, updated_at=excluded.updated_at
                        """,
                        (
                            store_id, spu, sku_name, product_code, safe_text(_val(row, c1_col)),
                            safe_text(_val(row, c2_col)), safe_text(_val(row, c3_col)),
                            safe_text(_val(row, brand_col)), safe_text(_val(row, status_col)), sku, now, now,
                        ),
                    )
                    spu_seen.add(spu)
                if sku:
                    duplicate += self._exists(conn, "dim_sku", "store_id=? AND sku=?", (store_id, sku))
                    conn.execute(
                        """
                        INSERT INTO dim_sku(store_id, sku, spu, product_code, sku_name, merchant_sku, sale_attr,
                            category_level1, category_level2, category_level3, brand, jd_price, stock,
                            status, product_url, short_title, created_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(store_id, sku) DO UPDATE SET
                            spu=excluded.spu, product_code=excluded.product_code, sku_name=excluded.sku_name,
                            merchant_sku=excluded.merchant_sku, sale_attr=excluded.sale_attr,
                            category_level1=excluded.category_level1, category_level2=excluded.category_level2,
                            category_level3=excluded.category_level3, brand=excluded.brand,
                            jd_price=excluded.jd_price, stock=excluded.stock, status=excluded.status,
                            product_url=excluded.product_url, short_title=excluded.short_title,
                            updated_at=excluded.updated_at
                        """,
                        (
                            store_id, sku, spu, product_code, sku_name, safe_text(_val(row, merchant_sku_col)),
                            safe_text(_val(row, sale_attr_col)), safe_text(_val(row, c1_col)),
                            safe_text(_val(row, c2_col)), safe_text(_val(row, c3_col)),
                            safe_text(_val(row, brand_col)), to_number(_val(row, price_col)),
                            to_int(_val(row, stock_col)), safe_text(_val(row, status_col)),
                            safe_text(_val(row, url_col)), safe_text(_val(row, short_col)), now, now,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO product_mapping(store_id, sku, spu, product_code, sku_name, spu_name,
                            bind_status, source, created_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, ?, 'active', 'product_master_sku', ?, ?)
                        ON CONFLICT(store_id, sku) DO UPDATE SET
                            spu=excluded.spu, product_code=excluded.product_code, sku_name=excluded.sku_name,
                            spu_name=excluded.spu_name, bind_status='active', source=excluded.source,
                            updated_at=excluded.updated_at
                        """,
                        (store_id, sku, spu, product_code, sku_name, sku_name, now, now),
                    )
                    success += 1
        result = {"total_rows": total, "success_rows": success, "skipped_rows": skipped, "duplicate_rows": duplicate, "unmatched_sku_count": 0}
        if log:
            self._log_success(store_id, "product_master_sku", file_path, result)
        return result

    def import_sku_daily(self, store_id: int, file_path: str | Path, date_start: str | None = None, log: bool = True) -> dict[str, int]:
        df = read_excel_flexible(file_path)
        date_s = normalize_report_date(df, file_path, date_start)
        sku_col = _first(df, ["SKU", "SKUID", *SKU_KEYS])
        if not sku_col:
            raise ValueError("SKU商品明细日报找不到 SKU 字段")
        cols = self._metric_cols(df)
        now = _now()
        total = success = skipped = duplicate = unmatched = 0
        with self.db.connect() as conn:
            for idx, row in df.iterrows():
                total += 1
                sku = safe_text(row.get(sku_col))
                date = normalize_date(date_s.loc[idx])
                if not sku or not date:
                    skipped += 1
                    continue
                mapping = self._mapping(conn, store_id, sku)
                if not mapping:
                    unmatched += 1
                    self._write_unmatched(conn, store_id, "product_detail_sku_daily", "fact_sku_daily", date, sku, None, None, file_path, row)
                    mapping = {"spu": "", "product_code": ""}
                duplicate += self._exists(conn, "fact_sku_daily", "store_id=? AND date=? AND sku=?", (store_id, date, sku))
                conn.execute(
                    """
                    INSERT INTO fact_sku_daily(store_id, date, sku, spu, product_code, sku_name,
                        transaction_amount, transaction_qty, transaction_order_count, transaction_customer_count,
                        transaction_conversion_rate, search_impressions, search_clicks, search_ctr,
                        product_views, product_visitors, add_to_cart_qty, add_to_cart_amount,
                        order_amount, cancel_refund_amount, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, date, sku) DO UPDATE SET
                        spu=excluded.spu, product_code=excluded.product_code, sku_name=excluded.sku_name,
                        transaction_amount=excluded.transaction_amount, transaction_qty=excluded.transaction_qty,
                        transaction_order_count=excluded.transaction_order_count,
                        transaction_customer_count=excluded.transaction_customer_count,
                        transaction_conversion_rate=excluded.transaction_conversion_rate,
                        search_impressions=excluded.search_impressions, search_clicks=excluded.search_clicks,
                        search_ctr=excluded.search_ctr, product_views=excluded.product_views,
                        product_visitors=excluded.product_visitors, add_to_cart_qty=excluded.add_to_cart_qty,
                        add_to_cart_amount=excluded.add_to_cart_amount, order_amount=excluded.order_amount,
                        cancel_refund_amount=excluded.cancel_refund_amount, updated_at=excluded.updated_at
                    """,
                    (store_id, date, sku, mapping["spu"], mapping["product_code"], safe_text(_val(row, cols["name"])),
                     to_number(_val(row, cols["transaction_amount"])), to_int(_val(row, cols["transaction_qty"])),
                     to_int(_val(row, cols["transaction_order_count"])), to_int(_val(row, cols["transaction_customer_count"])),
                     to_percent(_val(row, cols["transaction_conversion_rate"])), to_int(_val(row, cols["search_impressions"])),
                     to_int(_val(row, cols["search_clicks"])), to_percent(_val(row, cols["search_ctr"])),
                     to_int(_val(row, cols["product_views"])), to_int(_val(row, cols["product_visitors"])),
                     to_int(_val(row, cols["add_to_cart_qty"])), to_number(_val(row, cols["add_to_cart_amount"])),
                     to_number(_val(row, cols["order_amount"])), to_number(_val(row, cols["cancel_refund_amount"])), now, now),
                )
                success += 1
        result = {"total_rows": total, "success_rows": success, "skipped_rows": skipped, "duplicate_rows": duplicate, "unmatched_sku_count": unmatched}
        if log:
            self._log_success(store_id, "product_detail_sku_daily", file_path, result)
        return result

    def import_spu_daily(self, store_id: int, file_path: str | Path, date_start: str | None = None, log: bool = True) -> dict[str, int]:
        df = read_excel_flexible(file_path)
        date_s = normalize_report_date(df, file_path, date_start)
        spu_col = _first(df, ["SPU", "商品编码", *SPU_KEYS])
        if not spu_col:
            raise ValueError("SPU商品明细日报找不到 SPU/商品编码 字段")
        cols = self._metric_cols(df)
        now = _now()
        total = success = skipped = duplicate = 0
        with self.db.connect() as conn:
            for idx, row in df.iterrows():
                total += 1
                spu = safe_text(row.get(spu_col))
                date = normalize_date(date_s.loc[idx])
                if not spu or not date:
                    skipped += 1
                    continue
                product_code = self._spu_product_code(conn, store_id, spu) or safe_text(_val(row, _first(df, ITEM_NO_KEYS)))
                duplicate += self._exists(conn, "fact_spu_daily", "store_id=? AND date=? AND spu=?", (store_id, date, spu))
                conn.execute(
                    """
                    INSERT INTO fact_spu_daily(store_id, date, spu, product_code, spu_name,
                        transaction_amount, transaction_qty, transaction_order_count, transaction_customer_count,
                        transaction_conversion_rate, search_impressions, search_clicks, search_ctr,
                        product_views, product_visitors, add_to_cart_qty, add_to_cart_amount,
                        order_amount, cancel_refund_amount, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, date, spu) DO UPDATE SET
                        product_code=excluded.product_code, spu_name=excluded.spu_name,
                        transaction_amount=excluded.transaction_amount, transaction_qty=excluded.transaction_qty,
                        transaction_order_count=excluded.transaction_order_count,
                        transaction_customer_count=excluded.transaction_customer_count,
                        transaction_conversion_rate=excluded.transaction_conversion_rate,
                        search_impressions=excluded.search_impressions, search_clicks=excluded.search_clicks,
                        search_ctr=excluded.search_ctr, product_views=excluded.product_views,
                        product_visitors=excluded.product_visitors, add_to_cart_qty=excluded.add_to_cart_qty,
                        add_to_cart_amount=excluded.add_to_cart_amount, order_amount=excluded.order_amount,
                        cancel_refund_amount=excluded.cancel_refund_amount, updated_at=excluded.updated_at
                    """,
                    (store_id, date, spu, product_code, safe_text(_val(row, cols["name"])),
                     to_number(_val(row, cols["transaction_amount"])), to_int(_val(row, cols["transaction_qty"])),
                     to_int(_val(row, cols["transaction_order_count"])), to_int(_val(row, cols["transaction_customer_count"])),
                     to_percent(_val(row, cols["transaction_conversion_rate"])), to_int(_val(row, cols["search_impressions"])),
                     to_int(_val(row, cols["search_clicks"])), to_percent(_val(row, cols["search_ctr"])),
                     to_int(_val(row, cols["product_views"])), to_int(_val(row, cols["product_visitors"])),
                     to_int(_val(row, cols["add_to_cart_qty"])), to_number(_val(row, cols["add_to_cart_amount"])),
                     to_number(_val(row, cols["order_amount"])), to_number(_val(row, cols["cancel_refund_amount"])), now, now),
                )
                success += 1
        result = {"total_rows": total, "success_rows": success, "skipped_rows": skipped, "duplicate_rows": duplicate, "unmatched_sku_count": 0}
        if log:
            self._log_success(store_id, "product_detail_spu_daily", file_path, result)
        return result

    def import_search_rank(self, store_id: int, file_path: str | Path, date_start: str | None = None, log: bool = True) -> dict[str, int]:
        df = read_excel_flexible(file_path)
        date = extract_date_from_filename(file_path) or normalize_date(date_start)
        if not date:
            raise ValueError("搜索排名表没有日期字段，且文件名无法提取日期")
        sku_col = _first(df, ["SKU", "SKUID", *SKU_KEYS])
        if not sku_col:
            raise ValueError("搜索分析-排名定位找不到 SKU 字段")
        now = _now()
        total = success = skipped = duplicate = unmatched = 0
        with self.db.connect() as conn:
            for _, row in df.iterrows():
                total += 1
                sku = safe_text(row.get(sku_col))
                if not sku:
                    skipped += 1
                    continue
                mapping = self._mapping(conn, store_id, sku)
                if not mapping:
                    unmatched += 1
                    self._write_unmatched(conn, store_id, "search_rank_sku_daily", "fact_search_rank_daily", date, sku, None, None, file_path, row)
                    mapping = {"spu": "", "product_code": ""}
                mom = {str(c): safe_text(row.get(c)) for c in df.columns if "环比" in str(c)}
                duplicate += self._exists(conn, "fact_search_rank_daily", "store_id=? AND date=? AND sku=?", (store_id, date, sku))
                conn.execute(
                    """
                    INSERT INTO fact_search_rank_daily(store_id, date, sku, spu, product_code, product_info,
                        final_price, category_rank, impressions, impression_users, clicks, click_users,
                        transaction_amount, transaction_order_count, transaction_qty, transaction_customer_count,
                        search_ctr, search_conversion_rate, avg_order_value, mom_json, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, date, sku) DO UPDATE SET
                        spu=excluded.spu, product_code=excluded.product_code, product_info=excluded.product_info,
                        final_price=excluded.final_price, category_rank=excluded.category_rank,
                        impressions=excluded.impressions, impression_users=excluded.impression_users,
                        clicks=excluded.clicks, click_users=excluded.click_users,
                        transaction_amount=excluded.transaction_amount,
                        transaction_order_count=excluded.transaction_order_count,
                        transaction_qty=excluded.transaction_qty,
                        transaction_customer_count=excluded.transaction_customer_count,
                        search_ctr=excluded.search_ctr,
                        search_conversion_rate=excluded.search_conversion_rate,
                        avg_order_value=excluded.avg_order_value, mom_json=excluded.mom_json,
                        updated_at=excluded.updated_at
                    """,
                    (store_id, date, sku, mapping["spu"], mapping["product_code"],
                     safe_text(_val(row, _first(df, ["商品信息", "商品名称"]))),
                     to_number(_val(row, _first(df, ["到手价", "价格"]))),
                     to_int(_val(row, _first(df, ["类目排名", "排名"]))),
                     to_int(_val(row, _first(df, ["曝光次数"]))),
                     to_int(_val(row, _first(df, ["曝光人数"]))),
                     to_int(_val(row, _first(df, ["点击次数"]))),
                     to_int(_val(row, _first(df, ["点击人数"]))),
                     to_number(_val(row, _first(df, ["成交金额"]))),
                     to_int(_val(row, _first(df, ["成交单量"]))),
                     to_int(_val(row, _first(df, ["成交件数"]))),
                     to_int(_val(row, _first(df, ["成交人数"]))),
                     to_percent(_val(row, _first(df, ["搜索点击率"]))),
                     to_percent(_val(row, _first(df, ["搜索转化率"]))),
                     to_number(_val(row, _first(df, ["成交客单价"]))),
                     json.dumps(mom, ensure_ascii=False), now, now),
                )
                success += 1
        result = {"total_rows": total, "success_rows": success, "skipped_rows": skipped, "duplicate_rows": duplicate, "unmatched_sku_count": unmatched}
        if log:
            self._log_success(store_id, "search_rank_sku_daily", file_path, result)
        return result

    def import_order_items(self, store_id: int, file_path: str | Path, log: bool = True) -> dict[str, int]:
        df = read_excel_flexible(file_path)
        cols = {
            "date": _first(df, ["时间", "日期"]),
            "order_id": _first(df, ["订单号", "订单编号"]),
            "sku": _first(df, ["SKU", "SKUID", "商品编号", *SKU_KEYS]),
            "product_name": _first(df, ["商品名称"]),
            "pre": _first(df, ["优惠前金额"]),
            "qty": _first(df, ["成交订单商品件数", "商品件数", "件数"]),
            "discount": _first(df, ["优惠金额"]),
            "amount": _first(df, ["订单金额"]),
            "freight": _first(df, ["运费"]),
            "fee": _first(df, ["服务费"]),
            "order_time": _first(df, ["下单时间"]),
            "pay_time": _first(df, ["付款时间"]),
            "pay_method": _first(df, ["付款方式"]),
        }
        if not cols["order_id"] or not cols["sku"]:
            raise ValueError("成交订单明细至少需要 订单号 和 SKU 字段")
        return self._import_order_like(store_id, file_path, df, cols, "order_paid_items", "fact_order_items", log)

    def import_cancel_order_items(self, store_id: int, file_path: str | Path, log: bool = True) -> dict[str, int]:
        df = read_excel_flexible(file_path)
        cols = {
            "date": _first(df, ["时间", "日期"]),
            "order_id": _first(df, ["订单号", "订单编号"]),
            "sku": _first(df, ["SKU", "SKUID", "商品编号", *SKU_KEYS]),
            "spu": _first(df, ["SPU", "商品编码", *SPU_KEYS]),
            "product_name": _first(df, ["商品名称"]),
            "qty": _first(df, ["取消订单商品件数", "取消商品件数", "取消件数"]),
            "amount": _first(df, ["取消订单金额", "取消金额"]),
            "pay_time": _first(df, ["付款时间"]),
        }
        if not cols["order_id"] or not cols["sku"]:
            raise ValueError("取消订单明细至少需要 订单号 和 SKU 字段")

        now = _now()
        total = success = skipped = duplicate = unmatched = 0
        with self.db.connect() as conn:
            for _, row in df.iterrows():
                total += 1
                order_id = safe_text(_val(row, cols["order_id"]))
                sku = safe_text(_val(row, cols["sku"]))
                if not order_id or not sku:
                    skipped += 1
                    continue
                date = normalize_date(_val(row, cols["date"]), extract_date_from_filename(file_path))
                mapping = self._mapping(conn, store_id, sku)
                if not mapping:
                    unmatched += 1
                    self._write_unmatched(conn, store_id, "order_cancel_items", "fact_cancel_order_items", date, sku, order_id, None, file_path, row)
                    mapping = {"spu": safe_text(_val(row, cols["spu"])), "product_code": ""}
                duplicate += self._exists(conn, "fact_cancel_order_items", "store_id=? AND order_id=? AND sku=?", (store_id, order_id, sku))
                conn.execute(
                    """
                    INSERT INTO fact_cancel_order_items(store_id, date, order_id, sku, spu, product_code,
                        product_name, cancel_qty, cancel_amount, pay_time, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, order_id, sku) DO UPDATE SET
                        date=excluded.date, spu=excluded.spu, product_code=excluded.product_code,
                        product_name=excluded.product_name, cancel_qty=excluded.cancel_qty,
                        cancel_amount=excluded.cancel_amount, pay_time=excluded.pay_time,
                        updated_at=excluded.updated_at
                    """,
                    (store_id, date, order_id, sku, mapping["spu"], mapping["product_code"],
                     safe_text(_val(row, cols["product_name"])), to_int(_val(row, cols["qty"])),
                     to_number(_val(row, cols["amount"])), safe_text(_val(row, cols["pay_time"])), now, now),
                )
                success += 1
        result = {"total_rows": total, "success_rows": success, "skipped_rows": skipped, "duplicate_rows": duplicate, "unmatched_sku_count": unmatched}
        if log:
            self._log_success(store_id, "order_cancel_items", file_path, result)
        return result

    def import_refund_items(self, store_id: int, file_path: str | Path, log: bool = True) -> dict[str, int]:
        df = read_excel_flexible(file_path)
        cols = {
            "date": _first(df, ["时间", "日期"]),
            "service_id": _first(df, ["服务单ID", "服务单号"]),
            "order_id": _first(df, ["订单号", "订单编号"]),
            "sku": _first(df, ["SKU", "SKUID", "商品编号", *SKU_KEYS]),
            "product_name": _first(df, ["商品名称"]),
            "qty": _first(df, ["售后退款件数", "退款件数"]),
            "amount": _first(df, ["售后退款金额", "退款金额"]),
            "pay_time": _first(df, ["付款时间"]),
        }
        if not cols["service_id"] or not cols["order_id"] or not cols["sku"]:
            raise ValueError("售后退款订单至少需要 服务单ID、订单号 和 SKU 字段")
        now = _now()
        total = success = skipped = duplicate = unmatched = 0
        with self.db.connect() as conn:
            for _, row in df.iterrows():
                total += 1
                service_id = safe_text(_val(row, cols["service_id"]))
                order_id = safe_text(_val(row, cols["order_id"]))
                sku = safe_text(_val(row, cols["sku"]))
                if not service_id or not order_id or not sku:
                    skipped += 1
                    continue
                date = normalize_date(_val(row, cols["date"]), extract_date_from_filename(file_path))
                mapping = self._mapping(conn, store_id, sku)
                if not mapping:
                    unmatched += 1
                    self._write_unmatched(conn, store_id, "order_refund_items", "fact_refund_items", date, sku, order_id, service_id, file_path, row)
                    mapping = {"spu": "", "product_code": ""}
                duplicate += self._exists(conn, "fact_refund_items", "store_id=? AND service_id=? AND order_id=? AND sku=?", (store_id, service_id, order_id, sku))
                conn.execute(
                    """
                    INSERT INTO fact_refund_items(store_id, date, service_id, order_id, sku, spu, product_code,
                        product_name, refund_qty, refund_amount, pay_time, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, service_id, order_id, sku) DO UPDATE SET
                        date=excluded.date, spu=excluded.spu, product_code=excluded.product_code,
                        product_name=excluded.product_name, refund_qty=excluded.refund_qty,
                        refund_amount=excluded.refund_amount, pay_time=excluded.pay_time,
                        updated_at=excluded.updated_at
                    """,
                    (store_id, date, service_id, order_id, sku, mapping["spu"], mapping["product_code"],
                     safe_text(_val(row, cols["product_name"])), to_int(_val(row, cols["qty"])),
                     to_number(_val(row, cols["amount"])), safe_text(_val(row, cols["pay_time"])), now, now),
                )
                success += 1
        result = {"total_rows": total, "success_rows": success, "skipped_rows": skipped, "duplicate_rows": duplicate, "unmatched_sku_count": unmatched}
        if log:
            self._log_success(store_id, "order_refund_items", file_path, result)
        return result

    def import_aftersale_services(self, store_id: int, file_path: str | Path, log: bool = True) -> dict[str, int]:
        df = read_aftersale_excel(file_path)
        cols = {
            "service_id": _first(df, ["服务单号", "服务单ID"]),
            "order_id": _first(df, ["订单号"]),
            "sku": _first(df, ["商品编号", "SKU", "SKUID", *SKU_KEYS]),
            "product_name": _first(df, ["商品名称"]),
            "refund": _first(df, ["退款金额"]),
            "qty": _first(df, ["商品数量"]),
            "amount": _first(df, ["商品金额"]),
            "apply_time": _first(df, ["售后申请时间"]),
        }
        if not cols["service_id"] or not cols["order_id"] or not cols["sku"]:
            raise ValueError("自主售后服务单至少需要 服务单号、订单号 和 商品编号/SKU 字段")
        now = _now()
        total = success = skipped = duplicate = unmatched = 0
        with self.db.connect() as conn:
            for _, row in df.iterrows():
                total += 1
                service_id = safe_text(_val(row, cols["service_id"]))
                order_id = safe_text(_val(row, cols["order_id"]))
                sku = safe_text(_val(row, cols["sku"]))
                if not service_id or not order_id or not sku:
                    skipped += 1
                    continue
                mapping = self._mapping(conn, store_id, sku)
                if not mapping:
                    unmatched += 1
                    self._write_unmatched(conn, store_id, "aftersale_services", "fact_aftersale_services", normalize_date(_val(row, cols["apply_time"])), sku, order_id, service_id, file_path, row)
                    mapping = {"spu": "", "product_code": ""}
                duplicate += self._exists(conn, "fact_aftersale_services", "store_id=? AND service_id=? AND order_id=? AND sku=?", (store_id, service_id, order_id, sku))
                conn.execute(
                    """
                    INSERT INTO fact_aftersale_services(store_id, service_id, order_id, sku, spu, product_code,
                        customer_expectation, service_status, reason_level1, reason_level2, customer_problem,
                        return_method, apply_time, province, city, order_type, warehouse_status,
                        product_name, product_amount, product_qty, audit_result, audit_time, pickup_time,
                        waybill_no, express_company, merchant_receive_time, process_result, refund_amount,
                        is_flash_refund, service_duration, raw_json, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, service_id, order_id, sku) DO UPDATE SET
                        spu=excluded.spu, product_code=excluded.product_code,
                        customer_expectation=excluded.customer_expectation, service_status=excluded.service_status,
                        reason_level1=excluded.reason_level1, reason_level2=excluded.reason_level2,
                        customer_problem=excluded.customer_problem, return_method=excluded.return_method,
                        apply_time=excluded.apply_time, province=excluded.province, city=excluded.city,
                        order_type=excluded.order_type, warehouse_status=excluded.warehouse_status,
                        product_name=excluded.product_name, product_amount=excluded.product_amount,
                        product_qty=excluded.product_qty, audit_result=excluded.audit_result,
                        audit_time=excluded.audit_time, pickup_time=excluded.pickup_time,
                        waybill_no=excluded.waybill_no, express_company=excluded.express_company,
                        merchant_receive_time=excluded.merchant_receive_time,
                        process_result=excluded.process_result, refund_amount=excluded.refund_amount,
                        is_flash_refund=excluded.is_flash_refund, service_duration=excluded.service_duration,
                        raw_json=excluded.raw_json, updated_at=excluded.updated_at
                    """,
                    (store_id, service_id, order_id, sku, mapping["spu"], mapping["product_code"],
                     safe_text(_val(row, _first(df, ["客户期望"]))), safe_text(_val(row, _first(df, ["服务单状态"]))),
                     safe_text(_val(row, _first(df, ["一级申请原因"]))), safe_text(_val(row, _first(df, ["二级申请原因"]))),
                     safe_text(_val(row, _first(df, ["客户问题描述"]))), safe_text(_val(row, _first(df, ["返回方式"]))),
                     safe_text(_val(row, cols["apply_time"])), safe_text(_val(row, _first(df, ["省"]))),
                     safe_text(_val(row, _first(df, ["市"]))), safe_text(_val(row, _first(df, ["订单类型"]))),
                     safe_text(_val(row, _first(df, ["出库状态"]))), safe_text(_val(row, cols["product_name"])),
                     to_number(_val(row, cols["amount"])), to_int(_val(row, cols["qty"])),
                     safe_text(_val(row, _first(df, ["审核结果"]))), safe_text(_val(row, _first(df, ["审核时间"]))),
                     safe_text(_val(row, _first(df, ["取件时间"]))), safe_text(_val(row, _first(df, ["运单号"]))),
                     safe_text(_val(row, _first(df, ["快递公司"]))), safe_text(_val(row, _first(df, ["商家收货时间"]))),
                     safe_text(_val(row, _first(df, ["处理结果"]))), to_number(_val(row, cols["refund"])),
                     safe_text(_val(row, _first(df, ["是否闪退订单"]))), safe_text(_val(row, _first(df, ["售后整体时长"]))),
                     _json_row(row), now, now),
                )
                success += 1
        result = {"total_rows": total, "success_rows": success, "skipped_rows": skipped, "duplicate_rows": duplicate, "unmatched_sku_count": unmatched}
        if log:
            self._log_success(store_id, "aftersale_services", file_path, result)
        return result

    def _import_order_like(self, store_id: int, file_path: str | Path, df: pd.DataFrame, cols: dict[str, str | None], report_type: str, table: str, log: bool) -> dict[str, int]:
        now = _now()
        total = success = skipped = duplicate = unmatched = 0
        with self.db.connect() as conn:
            for _, row in df.iterrows():
                total += 1
                order_id = safe_text(_val(row, cols["order_id"]))
                sku = safe_text(_val(row, cols["sku"]))
                if not order_id or not sku:
                    skipped += 1
                    continue
                date = normalize_date(_val(row, cols["date"]), extract_date_from_filename(file_path))
                mapping = self._mapping(conn, store_id, sku)
                if not mapping:
                    unmatched += 1
                    self._write_unmatched(conn, store_id, report_type, table, date, sku, order_id, None, file_path, row)
                    mapping = {"spu": "", "product_code": ""}
                duplicate += self._exists(conn, table, "store_id=? AND order_id=? AND sku=?", (store_id, order_id, sku))
                conn.execute(
                    """
                    INSERT INTO fact_order_items(store_id, date, order_id, sku, spu, product_code, product_name,
                        pre_discount_amount, transaction_qty, discount_amount, order_amount, freight_amount,
                        service_fee, order_time, pay_time, payment_method, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, order_id, sku) DO UPDATE SET
                        date=excluded.date, spu=excluded.spu, product_code=excluded.product_code,
                        product_name=excluded.product_name, pre_discount_amount=excluded.pre_discount_amount,
                        transaction_qty=excluded.transaction_qty, discount_amount=excluded.discount_amount,
                        order_amount=excluded.order_amount, freight_amount=excluded.freight_amount,
                        service_fee=excluded.service_fee, order_time=excluded.order_time,
                        pay_time=excluded.pay_time, payment_method=excluded.payment_method,
                        updated_at=excluded.updated_at
                    """,
                    (store_id, date, order_id, sku, mapping["spu"], mapping["product_code"],
                     safe_text(_val(row, cols["product_name"])), to_number(_val(row, cols["pre"])),
                     to_int(_val(row, cols["qty"])), to_number(_val(row, cols["discount"])),
                     to_number(_val(row, cols["amount"])), to_number(_val(row, cols["freight"])),
                     to_number(_val(row, cols["fee"])), safe_text(_val(row, cols["order_time"])),
                     safe_text(_val(row, cols["pay_time"])), safe_text(_val(row, cols["pay_method"])), now, now),
                )
                success += 1
        result = {"total_rows": total, "success_rows": success, "skipped_rows": skipped, "duplicate_rows": duplicate, "unmatched_sku_count": unmatched}
        if log:
            self._log_success(store_id, report_type, file_path, result)
        return result

    def _metric_cols(self, df: pd.DataFrame) -> dict[str, str | None]:
        return {
            "name": _first(df, ["SKU名称", "SPU名称", "商品名称", "名称"]),
            "transaction_amount": _first(df, ["成交金额"]),
            "transaction_qty": _first(df, ["成交商品件数", "成交件数"]),
            "transaction_order_count": _first(df, ["成交单量"]),
            "transaction_customer_count": _first(df, ["成交客户数", "成交人数"]),
            "transaction_conversion_rate": _first(df, ["成交转化率"]),
            "search_impressions": _first(df, ["搜索曝光次数", "曝光次数"]),
            "search_clicks": _first(df, ["搜索点击次数", "点击次数"]),
            "search_ctr": _first(df, ["搜索点击率", "点击率"]),
            "product_views": _first(df, ["商品浏览量", "浏览量"]),
            "product_visitors": _first(df, ["商品访客数", "访客数"]),
            "add_to_cart_qty": _first(df, ["加购商品件数", "加购件数"]),
            "add_to_cart_amount": _first(df, ["加购金额"]),
            "order_amount": _first(df, ["下单金额"]),
            "cancel_refund_amount": _first(df, ["取消及售后退款金额", "售后退款金额"]),
        }

    def _mapping(self, conn, store_id: int, sku: str) -> dict[str, str] | None:
        row = conn.execute(
            "SELECT spu, product_code FROM product_mapping WHERE store_id=? AND sku=?",
            (store_id, safe_text(sku)),
        ).fetchone()
        return {"spu": safe_text(row["spu"]), "product_code": safe_text(row["product_code"])} if row else None

    def _spu_product_code(self, conn, store_id: int, spu: str) -> str:
        row = conn.execute("SELECT product_code FROM dim_spu WHERE store_id=? AND spu=?", (store_id, spu)).fetchone()
        return safe_text(row["product_code"]) if row else ""

    def _exists(self, conn, table: str, where: str, params: tuple[Any, ...]) -> int:
        row = conn.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", params).fetchone()
        return 1 if row else 0

    def _write_unmatched(self, conn, store_id: int, report_type: str, source_table: str, date: str | None, sku: str,
                         order_id: str | None, service_id: str | None, file_path: str | Path, row: pd.Series) -> None:
        conn.execute(
            """
            INSERT INTO data_quality_unmatched_sku(store_id, report_type, source_table, date, sku,
                order_id, service_id, file_name, file_path, raw_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (store_id, report_type, source_table, date, sku, order_id, service_id,
             Path(file_path).name, str(file_path), _json_row(row), _now()),
        )

    def _write_source_file(self, store_id: int, path: Path, report_type: str, date_start: str | None, date_end: str | None) -> None:
        now = _now()
        digest = _file_hash(path)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO source_files(file_hash, store_id, report_type, file_name, file_path, file_size,
                    date_start, date_end, imported_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    store_id=excluded.store_id, report_type=excluded.report_type,
                    file_name=excluded.file_name, file_path=excluded.file_path,
                    file_size=excluded.file_size, date_start=excluded.date_start,
                    date_end=excluded.date_end, imported_at=excluded.imported_at
                """,
                (digest, store_id, report_type, path.name, str(path), path.stat().st_size, date_start, date_end, now),
            )

    def _write_import_log(self, store_id: int, report_type: str, path: Path, date_start: str | None, date_end: str | None,
                          total: int, success: int, failed: int, skipped: int, duplicate: int, unmatched: int,
                          status: str, error_message: str, created_at: str | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO import_logs(store_id, report_type, file_name, file_path, date_start, date_end,
                    total_rows, success_rows, failed_rows, skipped_rows, duplicate_rows,
                    unmatched_sku_count, status, error_message, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (store_id, report_type, path.name, str(path), date_start, date_end, total, success,
                 failed, skipped, duplicate, unmatched, status, error_message, created_at or _now()),
            )

    def _log_success(self, store_id: int, report_type: str, file_path: str | Path, result: dict[str, int]) -> None:
        path = Path(file_path)
        date_start = extract_date_from_filename(path)
        self._write_source_file(store_id, path, report_type, date_start, date_start)
        self._write_import_log(
            store_id, report_type, path, date_start, date_start, result.get("total_rows", 0),
            result.get("success_rows", 0), 0, result.get("skipped_rows", 0),
            result.get("duplicate_rows", 0), result.get("unmatched_sku_count", 0), "success", "",
        )


class BaseInfoImporter(JDDataImporter):
    def import_base_info(self, store_id: int, file_path: str | Path) -> dict[str, int]:
        result = self.import_file(store_id, file_path, "product_master_sku")
        return {"spu": int(result["success_rows"]), "sku": int(result["success_rows"]), "mapping": int(result["success_rows"])}


def sync_summary_to_db(store_id: int, spu_df: pd.DataFrame, sku_df: pd.DataFrame, db: Database | None = None) -> dict[str, int]:
    db = db or Database()
    db.initialize()
    return {
        "fact_spu_daily": db.upsert_daily_spu(store_id, spu_df),
        "fact_sku_daily": db.upsert_daily_sku(store_id, sku_df),
    }
