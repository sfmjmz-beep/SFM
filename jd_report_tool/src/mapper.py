from __future__ import annotations

from pathlib import Path

import pandas as pd

from .database import Database
from .excel_processor import build_base_mappings
from .logger import logger
from .utils import safe_text


class ProductMapper:
    def __init__(self, store_id: int, db: Database | None = None, base_file: str | Path | None = None):
        self.store_id = store_id
        self.db = db or Database()
        self.spu_to_item: dict[str, str] = {}
        self.sku_to_mapping: dict[str, dict[str, str]] = {}
        try:
            self.spu_to_item.update(self.db.get_spu_item_no_map(store_id))
            self.sku_to_mapping.update(self.db.get_sku_mapping_map(store_id))
        except Exception as exc:
            logger.warning("从数据库读取映射失败: %s", exc)
        if base_file:
            spu_map, sku_map, _ = build_base_mappings(base_file)
            for spu, item in spu_map.items():
                self.spu_to_item.setdefault(spu, item)
            for sku, mapping in sku_map.items():
                self.sku_to_mapping.setdefault(sku, mapping)

    def add_spu_item(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        missing: list[str] = []
        item_values = []
        for spu in df["spu"].apply(safe_text):
            item = self.spu_to_item.get(spu, "")
            if not item and spu:
                missing.append(spu)
            item_values.append(item)
        df["货号"] = item_values
        if missing:
            logger.warning("找不到货号映射的 SPU 列表: %s", sorted(set(missing))[:200])
        return df

    def add_sku_mapping(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        missing: list[str] = []
        spus, items = [], []
        for sku in df["sku"].apply(safe_text):
            mapping = self.sku_to_mapping.get(sku, {})
            spu = safe_text(mapping.get("spu"))
            item = safe_text(mapping.get("item_no"))
            if not spu and sku:
                missing.append(sku)
            spus.append(spu)
            items.append(item)
        df["spu"] = spus
        df["货号"] = items
        if missing:
            logger.warning("找不到 SPU/货号映射的 SKU 列表: %s", sorted(set(missing))[:200])
        return df
