from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd
from fastapi import FastAPI, File, Query, UploadFile

from .database import Database
from .db_importer import BaseInfoImporter, JDDataImporter, sync_summary_to_db
from .logger import logger

app = FastAPI(title="JD Operation Local API", version="0.1.0")
db = Database()


@app.on_event("startup")
def startup() -> None:
    db.initialize()


@app.get("/stores")
def get_stores():
    return db.query("SELECT * FROM stores ORDER BY id")


@app.get("/products/spu")
def get_products_spu(store_id: int = Query(...)):
    return db.query("SELECT * FROM dim_spu WHERE store_id=? ORDER BY spu", (store_id,))


@app.get("/products/sku")
def get_products_sku(store_id: int = Query(...)):
    return db.query("SELECT * FROM dim_sku WHERE store_id=? ORDER BY sku", (store_id,))


@app.get("/mapping/by-sku")
def mapping_by_sku(store_id: int, sku: str):
    return db.get_mapping_by_sku(store_id, sku) or {}


@app.get("/mapping/by-spu")
def mapping_by_spu(store_id: int, spu: str):
    return db.get_mapping_by_spu(store_id, spu)


@app.get("/daily/spu")
def daily_spu(store_id: int, date_start: str, date_end: str):
    return db.query(
        "SELECT * FROM fact_spu_daily WHERE store_id=? AND date BETWEEN ? AND ? ORDER BY date, spu",
        (store_id, date_start, date_end),
    )


@app.get("/daily/sku")
def daily_sku(store_id: int, date_start: str, date_end: str):
    return db.query(
        "SELECT * FROM fact_sku_daily WHERE store_id=? AND date BETWEEN ? AND ? ORDER BY date, sku",
        (store_id, date_start, date_end),
    )


@app.get("/database/status")
def database_status(store_id: int | None = None):
    return {"summary": db.get_import_status_summary(store_id), "recent_logs": db.get_recent_import_logs(50)}


@app.post("/import/base-info")
async def import_base_info(store_id: int, file: UploadFile = File(...)):
    suffix = Path(file.filename or "base.xlsx").suffix or ".xlsx"
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    result = BaseInfoImporter(db).import_base_info(store_id, tmp_path)
    logger.info("API 导入基础信息: %s", result)
    return result


@app.post("/import/stage1")
async def import_stage1(
    store_id: int,
    report_type: str,
    date_start: str | None = None,
    date_end: str | None = None,
    file: UploadFile = File(...),
):
    suffix = Path(file.filename or "report.xlsx").suffix or ".xlsx"
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    result = JDDataImporter(db).import_file(store_id, tmp_path, report_type, date_start, date_end)
    logger.info("API 导入阶段1报表: %s", result)
    return result


@app.post("/sync/report-data")
async def sync_report_data(store_id: int, spu_file: UploadFile = File(...), sku_file: UploadFile = File(...)):
    with NamedTemporaryFile(delete=False, suffix=".xlsx") as s1:
        s1.write(await spu_file.read())
        spu_path = s1.name
    with NamedTemporaryFile(delete=False, suffix=".xlsx") as s2:
        s2.write(await sku_file.read())
        sku_path = s2.name
    spu_df = pd.read_excel(spu_path, sheet_name="SPU数据汇总", dtype={"spu": str, "货号": str})
    sku_df = pd.read_excel(sku_path, sheet_name="SKU数据汇总", dtype={"spu": str, "sku": str, "货号": str})
    return sync_summary_to_db(store_id, spu_df, sku_df, db)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run("src.api_server:app", host=host, port=port, reload=False)
