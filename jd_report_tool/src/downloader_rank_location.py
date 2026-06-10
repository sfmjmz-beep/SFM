from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .browser import BrowserManager, click_text, maybe_wait_captcha, screenshot_error
from .database import Database
from .logger import logger
from .utils import PROJECT_ROOT, unique_path

RANK_LOCATION_URL = "https://jdsz.jd.com/squidweb/view/flow/rankLocation.html"


class RankLocationDownloader:
    def __init__(self, browser: BrowserManager | None = None, db: Database | None = None):
        self.browser = browser or BrowserManager()
        self.db = db or Database()

    async def _set_date(self, page, day: str) -> None:
        logger.info("设置商品排名定位日期: %s", day)
        inputs = page.locator("input")
        for i in range(await inputs.count()):
            try:
                inp = inputs.nth(i)
                placeholder = await inp.get_attribute("placeholder") or ""
                value = await inp.input_value(timeout=500)
                if "日期" in placeholder or "20" in value:
                    await inp.fill(day)
                    return
            except Exception:
                continue
        logger.warning("未能自动填写排名定位日期，请确认页面日期")

    async def download(self, store: dict, day: str) -> Path | None:
        task_name = "商品排名定位"
        page = await self.browser.new_page()
        logger.info("任务名称=%s 店铺=%s 日期=%s URL=%s", task_name, store.get("store_name"), day, RANK_LOCATION_URL)
        try:
            await page.goto(RANK_LOCATION_URL, wait_until="domcontentloaded")
            await maybe_wait_captcha(page)
            await page.wait_for_load_state("networkidle", timeout=30000)
            await click_text(page, "离线")
            await self._set_date(page, day)
            await click_text(page, "商品排名")
            await click_text(page, "查询")
            await page.wait_for_timeout(5000)
            save_dir = PROJECT_ROOT / "data" / "raw" / "商品排名定位" / day
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = unique_path(save_dir / f"商品排名定位_{store.get('store_name')}_{day}.xlsx")
            async with page.expect_download(timeout=60000) as download_info:
                await click_text(page, "下载数据")
            download = await download_info.value
            source_name = download.suggested_filename
            await download.save_as(str(save_path))
            logger.info("下载完成 原始文件名=%s 重命名后=%s 保存路径=%s", source_name, save_path.name, save_path)
            self.db.upsert_report_file(
                store_id=store.get("store_id"), report_type=task_name, date_start=day, date_end=day,
                source_file_name=source_name, saved_file_path=str(save_path),
                download_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status="success", error_msg="",
            )
            return save_path
        except Exception as exc:
            logger.exception("商品排名定位下载失败: %s", exc)
            try:
                await screenshot_error(page, task_name)
            except Exception:
                pass
            self.db.upsert_report_file(
                store_id=store.get("store_id"), report_type=task_name, date_start=day, date_end=day,
                source_file_name="", saved_file_path="", download_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                status="failed", error_msg=str(exc),
            )
            return None
