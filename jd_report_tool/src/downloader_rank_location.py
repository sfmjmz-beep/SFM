from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from .browser import BrowserManager, click_calendar_date, click_text, maybe_wait_captcha, screenshot_error
from .database import Database
from .logger import logger
from .utils import PROJECT_ROOT, normalize_date, unique_path

RANK_LOCATION_URL = "https://jdsz.jd.com/squidweb/view/flow/rankLocation.html"


class RankLocationDownloader:
    def __init__(self, browser: BrowserManager | None = None, db: Database | None = None):
        self.browser = browser or BrowserManager()
        self.db = db or Database()
        self._last_selected_day: str | None = None

    async def _set_date(self, page, day: str) -> None:
        logger.info("设置商品排名定位日期: %s", day)
        await self._click_date_filter(page)
        await page.wait_for_timeout(500)
        await click_calendar_date(page, day)
        self._last_selected_day = day
        await page.wait_for_timeout(1000)

    async def _click_date_filter(self, page) -> None:
        result = await page.evaluate(
            """
            () => {
              const visible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none' &&
                  rect.width > 0 && rect.height > 0;
              };
              const all = Array.from(document.querySelectorAll('button, input, div, span')).filter(visible);
              const candidates = all
                .filter((el) => {
                  const text = (el.innerText || el.value || el.textContent || '').trim();
                  return text.includes('按天查询') || /20\\d{2}-\\d{2}-\\d{2}\\s*至\\s*20\\d{2}-\\d{2}-\\d{2}/.test(text);
                })
                .map((el) => {
                  const rect = el.getBoundingClientRect();
                  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                })
                .sort((a, b) => (b.x - a.x) || (a.y - b.y));
              return candidates[0] || null;
            }
            """
        )
        if not result:
            raise TimeoutError("未找到商品排名定位日期筛选控件")
        await page.mouse.click(result["x"] + result["width"] / 2, result["y"] + result["height"] / 2)
        logger.info("点击商品排名定位日期筛选控件")

    async def _click_product_rank_tab(self, page) -> None:
        target = page.get_by_text("商品排名", exact=True)
        count = await target.count()
        for i in range(count):
            item = target.nth(i)
            if await item.is_visible(timeout=500):
                await item.click(timeout=10000)
                logger.info("点击商品排名 Tab")
                return
        raise TimeoutError("未找到可点击的商品排名 Tab")

    async def _selected_date_range(self, page) -> tuple[str, str] | None:
        text = await page.evaluate(
            """
            () => Array.from(document.querySelectorAll('body *'))
              .map((el) => [el.innerText, el.textContent, el.value, el.getAttribute('title'), el.getAttribute('aria-label')]
                .filter(Boolean).join(' '))
              .filter(Boolean)
              .join('\\n')
            """
        )
        match = re.search(
            r"按天查询\s*[（(]\s*(20\d{2}-\d{2}-\d{2})\s*至\s*(20\d{2}-\d{2}-\d{2})\s*[）)]",
            text,
        )
        if match:
            return match.group(1), match.group(2)
        match = re.search(r"(20\d{2}-\d{2}-\d{2})\s*至\s*(20\d{2}-\d{2}-\d{2})", text)
        if match:
            return match.group(1), match.group(2)
        return None

    async def _assert_selected_date(self, page, day: str) -> None:
        selected = None
        for _ in range(20):
            selected = await self._selected_date_range(page)
            if selected == (day, day):
                logger.info("商品排名定位页面日期校验通过: %s 至 %s", selected[0], selected[1])
                return
            await page.wait_for_timeout(500)
        if selected is None:
            if self._last_selected_day == day:
                logger.warning("未读取到商品排名定位右上角日期，使用刚设置的目标日期继续: %s 至 %s", day, day)
                return
            raise AssertionError("未读取到商品排名定位右上角日期，停止下载")
        raise AssertionError(
            f"商品排名定位页面日期不匹配，停止下载: actual={selected[0]} 至 {selected[1]} expected={day} 至 {day}"
        )

    async def download(self, store: dict, day: str) -> Path | None:
        day = normalize_date(day) or day
        task_name = "商品排名定位"
        page = await self.browser.new_page()
        logger.info("任务名称=%s 店铺=%s 日期=%s URL=%s", task_name, store.get("store_name"), day, RANK_LOCATION_URL)
        try:
            await page.goto(RANK_LOCATION_URL, wait_until="domcontentloaded")
            await maybe_wait_captcha(page)
            await page.wait_for_load_state("networkidle", timeout=30000)
            await click_text(page, "离线")
            await self._click_product_rank_tab(page)
            await self._set_date(page, day)
            await click_text(page, "查询")
            await page.wait_for_timeout(5000)
            await page.wait_for_load_state("networkidle", timeout=30000)
            await self._assert_selected_date(page, day)
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
