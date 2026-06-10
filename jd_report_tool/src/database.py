from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


from .db_models import INDEX_SQL, SCHEMA_SQL
from .logger import logger
from .utils import PROJECT_ROOT, ensure_dirs, load_json, safe_text

DB_PATH = PROJECT_ROOT / "data" / "database" / "jd_operation.db"


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
            conn.executescript(SCHEMA_SQL)
            for sql in INDEX_SQL:
                conn.execute(sql)
        self.sync_stores_from_config()
        logger.info("数据库初始化完成: %s", self.db_path)

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
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO report_files(store_id, report_type, date_start, date_end, source_file_name,
                    saved_file_path, download_time, status, error_msg, created_at)
                VALUES(:store_id, :report_type, :date_start, :date_end, :source_file_name,
                    :saved_file_path, :download_time, :status, :error_msg, :created_at)
                """,
                {**kwargs, "created_at": now},
            )

    def get_mapping_by_sku(self, store_id: int, sku: str) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT store_id, spu, sku, item_no FROM product_mapping WHERE store_id=? AND sku=?",
            (store_id, safe_text(sku)),
        )
        return rows[0] if rows else None

    def get_mapping_by_spu(self, store_id: int, spu: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT store_id, spu, sku, item_no FROM product_mapping WHERE store_id=? AND spu=? ORDER BY sku",
            (store_id, safe_text(spu)),
        )

    def get_spu_item_no_map(self, store_id: int) -> dict[str, str]:
        rows = self.query("SELECT spu, item_no FROM products_spu WHERE store_id=?", (store_id,))
        return {safe_text(r["spu"]): safe_text(r["item_no"]) for r in rows if safe_text(r.get("spu"))}

    def get_sku_mapping_map(self, store_id: int) -> dict[str, dict[str, str]]:
        rows = self.query("SELECT sku, spu, item_no FROM product_mapping WHERE store_id=?", (store_id,))
        return {safe_text(r["sku"]): {"spu": safe_text(r["spu"]), "item_no": safe_text(r["item_no"])} for r in rows if safe_text(r.get("sku"))}

    def upsert_daily_spu(self, store_id: int, df) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.connect() as conn:
            for _, row in df.iterrows():
                if not safe_text(row.get("spu")) or not safe_text(row.get("时间")):
                    continue
                conn.execute(
                    """
                    INSERT INTO daily_spu_data(date, store_id, spu, item_no, exposure, clicks, ctr,
                        order_qty, conversion_rate, gmv, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, store_id, spu) DO UPDATE SET
                        item_no=excluded.item_no, exposure=excluded.exposure, clicks=excluded.clicks,
                        ctr=excluded.ctr, order_qty=excluded.order_qty,
                        conversion_rate=excluded.conversion_rate, gmv=excluded.gmv, updated_at=excluded.updated_at
                    """,
                    (
                        safe_text(row.get("时间")), store_id, safe_text(row.get("spu")), safe_text(row.get("货号")),
                        row.get("曝光次数"), row.get("点击次数"), row.get("曝光点击率"), row.get("成交商品件数"),
                        row.get("曝光转化率"), row.get("成交金额"), now, now,
                    ),
                )
                count += 1
        logger.info("写入 daily_spu_data %s 行", count)
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
                    INSERT INTO daily_sku_data(date, store_id, spu, item_no, sku, exposure, clicks, ctr,
                        order_qty, conversion_rate, gmv, category_rank, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, store_id, sku) DO UPDATE SET
                        spu=excluded.spu, item_no=excluded.item_no, exposure=excluded.exposure,
                        clicks=excluded.clicks, ctr=excluded.ctr, order_qty=excluded.order_qty,
                        conversion_rate=excluded.conversion_rate, gmv=excluded.gmv,
                        category_rank=excluded.category_rank, updated_at=excluded.updated_at
                    """,
                    (
                        safe_text(row.get("时间")), store_id, safe_text(row.get("spu")), safe_text(row.get("货号")),
                        safe_text(row.get("sku")), row.get("曝光次数"), row.get("点击次数"), row.get("曝光点击率"),
                        row.get("成交商品件数"), row.get("曝光转化率"), row.get("成交金额"), row.get("类目排名"), now, now,
                    ),
                )
                count += 1
        logger.info("写入 daily_sku_data %s 行", count)
        return count
