from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


from .db_models import INDEX_SQL, TABLE_COLUMNS, UNIQUE_INDEXES
from .logger import logger
from .utils import PROJECT_ROOT, ensure_dirs, load_json, safe_text

DB_PATH = PROJECT_ROOT / "data" / "jd_ops.db"


class Database:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        ensure_dirs()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            self._ensure_tables(conn)
            for sql in INDEX_SQL:
                conn.execute(sql)
            for name, table, columns in UNIQUE_INDEXES:
                cols = ", ".join(columns)
                conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table}({cols})")
        self.sync_stores_from_config()
        logger.info("数据库初始化完成: %s", self.db_path)

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        for table, columns in TABLE_COLUMNS.items():
            column_sql = ", ".join(f"{name} {definition}" for name, definition in columns)
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({column_sql})")
            existing = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns:
                if name in existing:
                    continue
                # SQLite cannot add a PRIMARY KEY column after creation. All managed tables
                # are created with id initially; this guard keeps upgrades non-destructive.
                if "PRIMARY KEY" in definition.upper():
                    continue
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def sync_stores_from_config(self) -> None:
        cfg = PROJECT_ROOT / "config" / "stores.json"
        if not cfg.exists():
            return
        stores = load_json(cfg).get("stores", [])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as conn:
            for s in stores:
                conn.execute(
                    """
                    INSERT INTO stores(id, store_name, shop_id, platform, enabled, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        store_name=excluded.store_name, shop_id=excluded.shop_id,
                        platform=excluded.platform, enabled=excluded.enabled, updated_at=excluded.updated_at
                    """,
                    (
                        s.get("store_id"), s.get("store_name"), safe_text(s.get("shop_id")),
                        s.get("platform", "JD"), 1 if s.get("enabled", True) else 0, now, now,
                    ),
                )

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self.connect() as conn:
            conn.execute(sql, tuple(params))

    def upsert_report_file(self, **kwargs: Any) -> None:
        self.write_download_log(
            store_id=kwargs.get("store_id"),
            store_name=kwargs.get("store_name", ""),
            report_type=kwargs.get("report_type", ""),
            report_name=kwargs.get("report_name", kwargs.get("report_type", "")),
            start_date=kwargs.get("date_start", kwargs.get("start_date", "")),
            end_date=kwargs.get("date_end", kwargs.get("end_date", "")),
            page_url=kwargs.get("page_url", ""),
            file_name=kwargs.get("source_file_name", kwargs.get("file_name", "")),
            file_path=kwargs.get("saved_file_path", kwargs.get("file_path", "")),
            download_status=kwargs.get("status", kwargs.get("download_status", "")),
            download_time=kwargs.get("download_time", ""),
            error_message=kwargs.get("error_msg", kwargs.get("error_message", "")),
            screenshot_path=kwargs.get("screenshot_path", ""),
        )

    def write_download_log(
        self,
        *,
        store_id: Any,
        store_name: str = "",
        report_type: str,
        report_name: str,
        start_date: str,
        end_date: str,
        page_url: str,
        file_name: str = "",
        file_path: str = "",
        download_status: str,
        download_time: str = "",
        error_message: str = "",
        screenshot_path: str = "",
    ) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        actual_download_time = download_time or now
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO download_logs(
                    store_id, store_name, report_type, report_name,
                    start_date, end_date, date_start, date_end, page_url,
                    file_name, file_path, download_status, status,
                    download_time, error_message, screenshot_path, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    store_id,
                    store_name,
                    report_type,
                    report_name,
                    start_date,
                    end_date,
                    start_date,
                    end_date,
                    page_url,
                    file_name,
                    file_path,
                    download_status,
                    download_status,
                    actual_download_time,
                    error_message,
                    screenshot_path,
                    now,
                ),
            )

    def get_mapping_by_sku(self, store_id: int, sku: str) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT store_id, spu, sku, product_code FROM product_mapping WHERE store_id=? AND sku=?",
            (store_id, safe_text(sku)),
        )
        return rows[0] if rows else None

    def get_mapping_by_spu(self, store_id: int, spu: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT store_id, spu, sku, product_code FROM product_mapping WHERE store_id=? AND spu=? ORDER BY sku",
            (store_id, safe_text(spu)),
        )

    def get_spu_item_no_map(self, store_id: int) -> dict[str, str]:
        rows = self.query("SELECT spu, product_code FROM dim_spu WHERE store_id=?", (store_id,))
        return {safe_text(r["spu"]): safe_text(r["product_code"]) for r in rows if safe_text(r.get("spu"))}

    def get_sku_mapping_map(self, store_id: int) -> dict[str, dict[str, str]]:
        rows = self.query("SELECT sku, spu, product_code FROM product_mapping WHERE store_id=?", (store_id,))
        return {safe_text(r["sku"]): {"spu": safe_text(r["spu"]), "item_no": safe_text(r["product_code"]), "product_code": safe_text(r["product_code"])} for r in rows if safe_text(r.get("sku"))}

    def upsert_daily_spu(self, store_id: int, df) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.connect() as conn:
            for _, row in df.iterrows():
                if not safe_text(row.get("spu")) or not safe_text(row.get("时间")):
                    continue
                conn.execute(
                    """
                    INSERT INTO fact_spu_daily(date, store_id, spu, product_code, search_impressions, search_clicks, search_ctr,
                        transaction_qty, transaction_conversion_rate, transaction_amount, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, date, spu) DO UPDATE SET
                        product_code=excluded.product_code, search_impressions=excluded.search_impressions, search_clicks=excluded.search_clicks,
                        search_ctr=excluded.search_ctr, transaction_qty=excluded.transaction_qty,
                        transaction_conversion_rate=excluded.transaction_conversion_rate, transaction_amount=excluded.transaction_amount, updated_at=excluded.updated_at
                    """,
                    (
                        safe_text(row.get("时间")), store_id, safe_text(row.get("spu")), safe_text(row.get("货号")),
                        row.get("曝光次数"), row.get("点击次数"), row.get("曝光点击率"), row.get("成交商品件数"),
                        row.get("曝光转化率"), row.get("成交金额"), now, now,
                    ),
                )
                count += 1
        logger.info("写入 fact_spu_daily %s 行", count)
        return count

    def upsert_daily_sku(self, store_id: int, df) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.connect() as conn:
            for _, row in df.iterrows():
                if not safe_text(row.get("sku")) or not safe_text(row.get("时间")):
                    continue
                conn.execute(
                    """
                    INSERT INTO fact_sku_daily(date, store_id, spu, product_code, sku, search_impressions, search_clicks, search_ctr,
                        transaction_qty, transaction_conversion_rate, transaction_amount, category_rank, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, date, sku) DO UPDATE SET
                        spu=excluded.spu, product_code=excluded.product_code, search_impressions=excluded.search_impressions,
                        search_clicks=excluded.search_clicks, search_ctr=excluded.search_ctr, transaction_qty=excluded.transaction_qty,
                        transaction_conversion_rate=excluded.transaction_conversion_rate, transaction_amount=excluded.transaction_amount,
                        category_rank=excluded.category_rank, updated_at=excluded.updated_at
                    """,
                    (
                        safe_text(row.get("时间")), store_id, safe_text(row.get("spu")), safe_text(row.get("货号")),
                        safe_text(row.get("sku")), row.get("曝光次数"), row.get("点击次数"), row.get("曝光点击率"),
                        row.get("成交商品件数"), row.get("曝光转化率"), row.get("成交金额"), row.get("类目排名"), now, now,
                    ),
                )
                count += 1
        logger.info("写入 fact_sku_daily %s 行", count)
        return count

    def get_import_status_summary(self, store_id: int | None = None) -> dict[str, Any]:
        where = "WHERE store_id=?" if store_id is not None else ""
        params: tuple[Any, ...] = (store_id,) if store_id is not None else ()
        def scalar(sql: str, p: Iterable[Any] = params) -> Any:
            rows = self.query(sql, p)
            return next(iter(rows[0].values())) if rows else None

        if store_id is None:
            latest = self.query("SELECT created_at, report_type FROM import_logs ORDER BY created_at DESC, id DESC LIMIT 1")
            failed_files = scalar("SELECT COUNT(*) FROM import_logs WHERE status='failed'", ())
        else:
            latest = self.query(
                "SELECT created_at, report_type FROM import_logs WHERE store_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                (store_id,),
            )
            failed_files = scalar("SELECT COUNT(*) FROM import_logs WHERE store_id=? AND status='failed'", (store_id,))
        return {
            "db_path": str(self.db_path),
            "stores": scalar("SELECT COUNT(*) FROM stores", ()),
            "spu": scalar(f"SELECT COUNT(*) FROM dim_spu {where}", params),
            "sku": scalar(f"SELECT COUNT(*) FROM dim_sku {where}", params),
            "mapping": scalar(f"SELECT COUNT(*) FROM product_mapping {where}", params),
            "orders": scalar(f"SELECT COUNT(*) FROM fact_order_items {where}", params),
            "cancel_orders": scalar(f"SELECT COUNT(*) FROM fact_cancel_order_items {where}", params),
            "refunds": scalar(f"SELECT COUNT(*) FROM fact_refund_items {where}", params),
            "aftersales": scalar(f"SELECT COUNT(*) FROM fact_aftersale_services {where}", params),
            "search_rank_dates": scalar(f"SELECT COUNT(DISTINCT date) FROM fact_search_rank_daily {where}", params),
            "latest_import_time": latest[0]["created_at"] if latest else "",
            "latest_import_type": latest[0]["report_type"] if latest else "",
            "unmatched_sku": scalar(f"SELECT COUNT(*) FROM data_quality_unmatched_sku {where}", params),
            "failed_files": failed_files,
        }

    def get_recent_import_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT created_at, store_id, report_type, file_name, total_rows, success_rows,
                   failed_rows, skipped_rows, duplicate_rows, unmatched_sku_count, status, error_message
            FROM import_logs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
