from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .database import Database
from .excel_processor import ITEM_NO_KEYS, SKU_KEYS, SPU_KEYS, find_col, find_col_any, read_excel_flexible
from .logger import logger
from .utils import safe_text, to_int, to_number


class BaseInfoImporter:
    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def import_base_info(self, store_id: int, file_path: str | Path) -> dict[str, int]:
        self.db.initialize()
        df = read_excel_flexible(file_path)
        sku_col = find_col_any(df, SKU_KEYS)
        spu_col = find_col_any(df, SPU_KEYS)
        item_col = find_col_any(df, ITEM_NO_KEYS)
        title_col = find_col_any(df, ["商品标题", "商品名称", "标题", "商品名"])
        sku_name_col = find_col_any(df, ["SKU名称", "sku名称", "销售属性", "规格属性", "SKU规格"]) or title_col
        category_col = find_col_any(df, ["类目", "叶子类目", "三级类目", "末级类目"])
        brand_col = find_col_any(df, ["品牌", "品牌名称"])
        status_col = find_col_any(df, ["状态", "商品状态", "SKU状态"])
        price_col = find_col_any(df, ["京东价", "价格", "销售价", "商品价格"])
        stock_col = find_col_any(df, ["库存", "可售库存", "库存数量"])
        color_col = find_col_any(df, ["颜色", "颜色分类", "色系"])
        config_col = find_col_any(df, ["规格", "型号", "配置", "销售规格"])
        if not sku_col or not spu_col:
            raise ValueError("商品基础信息表至少需要 SKU 和 SPU/商品编码 字段")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        spu_seen: set[str] = set()
        sku_count = mapping_count = 0
        with self.db.connect() as conn:
            for _, row in df.iterrows():
                sku = safe_text(row.get(sku_col))
                spu = safe_text(row.get(spu_col))
                if not sku and not spu:
                    continue
                item_no = safe_text(row.get(item_col)) if item_col else ""
                if spu and spu not in spu_seen:
                    conn.execute(
                        """
                        INSERT INTO products_spu(store_id, spu, item_no, product_title, category, brand, status,
                            main_sku, remark, created_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(store_id, spu) DO UPDATE SET
                            item_no=excluded.item_no, product_title=excluded.product_title,
                            category=excluded.category, brand=excluded.brand, status=excluded.status,
                            updated_at=excluded.updated_at
                        """,
                        (
                            store_id, spu, item_no, safe_text(row.get(title_col)) if title_col else "",
                            safe_text(row.get(category_col)) if category_col else "",
                            safe_text(row.get(brand_col)) if brand_col else "",
                            safe_text(row.get(status_col)) if status_col else "",
                            sku, "", now, now,
                        ),
                    )
                    spu_seen.add(spu)
                if sku:
                    conn.execute(
                        """
                        INSERT INTO products_sku(store_id, spu, sku, item_no, sku_name, color, config, price,
                            stock, created_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(store_id, sku) DO UPDATE SET
                            spu=excluded.spu, item_no=excluded.item_no, sku_name=excluded.sku_name,
                            color=excluded.color, config=excluded.config, price=excluded.price,
                            stock=excluded.stock, updated_at=excluded.updated_at
                        """,
                        (
                            store_id, spu, sku, item_no, safe_text(row.get(sku_name_col)) if sku_name_col else "",
                            safe_text(row.get(color_col)) if color_col else "",
                            safe_text(row.get(config_col)) if config_col else "",
                            to_number(row.get(price_col)) if price_col else None,
                            to_int(row.get(stock_col)) if stock_col else None, now, now,
                        ),
                    )
                    sku_count += 1
                    conn.execute(
                        """
                        INSERT INTO product_mapping(store_id, spu, sku, item_no, bind_status, created_at, updated_at)
                        VALUES(?, ?, ?, ?, 'active', ?, ?)
                        ON CONFLICT(store_id, sku) DO UPDATE SET
                            spu=excluded.spu, item_no=excluded.item_no, bind_status='active', updated_at=excluded.updated_at
                        """,
                        (store_id, spu, sku, item_no, now, now),
                    )
                    mapping_count += 1
        result = {"spu": len(spu_seen), "sku": sku_count, "mapping": mapping_count}
        logger.info("导入商品基础信息完成: %s", result)
        return result


def sync_summary_to_db(store_id: int, spu_df: pd.DataFrame, sku_df: pd.DataFrame, db: Database | None = None) -> dict[str, int]:
    db = db or Database()
    db.initialize()
    return {
        "daily_spu": db.upsert_daily_spu(store_id, spu_df),
        "daily_sku": db.upsert_daily_sku(store_id, sku_df),
    }
