from __future__ import annotations

from datetime import datetime
from pathlib import Path

from playwright.async_api import Page

from .browser import BrowserManager, click_text, maybe_wait_captcha, screenshot_error
from .database import Database
from .logger import logger
from .utils import PROJECT_ROOT, unique_path

PRODUCT_DETAIL_URL = "https://jdsz.jd.com/szweb/view/product/productDetail.html"


class ProductDetailDownloader:
    def __init__(self, browser: BrowserManager | None = None, db: Database | None = None):
        self.browser = browser or BrowserManager()
        self.db = db or Database()

    async def _set_date(self, page: Page, date_start: str, date_end: str) -> None:
        logger.info("设置日期范围: %s ~ %s", date_start, date_end)
        try:
            await click_text(page, "自定义", timeout=5000)
        except Exception:
            pass
        inputs = page.locator("input")
        count = await inputs.count()
        filled = 0
        for i in range(count):
            inp = inputs.nth(i)
            try:
                placeholder = await inp.get_attribute("placeholder") or ""
                value = await inp.input_value(timeout=500)
                if any(k in placeholder for k in ["日期", "开始", "结束"]) or "20" in value:
                    await inp.fill(date_start if filled == 0 else date_end)
                    filled += 1
                    if filled >= 2:
                        break
            except Exception:
                continue
        if filled < 2:
            logger.warning("日期输入框自动识别不足，请检查页面是否已选中目标日期")

    async def download(self, store: dict, tab: str, date_start: str, date_end: str) -> Path | None:
        task_name = f"商品明细-{tab}"
        page = await self.browser.new_page()
        start_time = datetime.now()
        logger.info("任务名称=%s 店铺=%s 日期=%s~%s URL=%s", task_name, store.get("store_name"), date_start, date_end, PRODUCT_DETAIL_URL)
        try:
            await page.goto(PRODUCT_DETAIL_URL, wait_until="domcontentloaded")
            await maybe_wait_captcha(page)
            await page.wait_for_load_state("networkidle", timeout=30000)
            await self._set_date(page, date_start, date_end)
            await click_text(page, tab)
            await click_text(page, "查询")
            await page.wait_for_timeout(5000)
            await page.wait_for_load_state("networkidle", timeout=30000)
            logger.info("点击下载数据前时间: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
            await click_text(page, "下载数据")
            await page.wait_for_timeout(1000)
            await click_text(page, "分天下载")
            await click_text(page, "不包含对比时间")
            await click_text(page, "确定")
            try:
                await click_text(page, "前往查看", timeout=15000)
            except Exception:
                logger.warning("未找到前往查看，尝试直接查找下载中心/等待任务生成")
            save_dir = PROJECT_ROOT / "data" / "raw" / f"商品明细_{tab}" / date_end
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = unique_path(save_dir / f"商品明细_{tab}_{store.get('store_name')}_{date_start}_{date_end}.xlsx")
            deadline = datetime.now().timestamp() + 300
            while datetime.now().timestamp() < deadline:
                await maybe_wait_captcha(page)
                try:
                    row = page.locator("tr", has_text="已生成").first
                    if await row.count():
                        async with page.expect_download(timeout=30000) as download_info:
                            try:
                                await row.get_by_text("下载", exact=False).last.click()
                            except Exception:
                                await click_text(page, "下载")
                        download = await download_info.value
                        source_name = download.suggested_filename
                        await download.save_as(str(save_path))
                        logger.info("下载完成 原始文件名=%s 重命名后=%s 保存路径=%s", source_name, save_path.name, save_path)
                        self.db.upsert_report_file(
                            store_id=store.get("store_id"), report_type=task_name, date_start=date_start, date_end=date_end,
                            source_file_name=source_name, saved_file_path=str(save_path),
                            download_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status="success", error_msg="",
                        )
                        return save_path
                except Exception as exc:
                    logger.warning("下载中心轮询暂未成功: %s", exc)
                await page.wait_for_timeout(10000)
                try:
                    await page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
            raise TimeoutError("下载中心 5 分钟内未生成文件")
        except Exception as exc:
            logger.exception("%s 下载失败: %s", task_name, exc)
            try:
                await screenshot_error(page, task_name)
            except Exception:
                pass
            self.db.upsert_report_file(
                store_id=store.get("store_id"), report_type=task_name, date_start=date_start, date_end=date_end,
                source_file_name="", saved_file_path="", download_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                status="failed", error_msg=str(exc),
            )
            return None
