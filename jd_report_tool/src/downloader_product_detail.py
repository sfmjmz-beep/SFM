from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from playwright.async_api import Page

from .browser import BrowserManager, click_text, maybe_wait_captcha, screenshot_error, select_calendar_range
from .database import Database
from .logger import logger
from .utils import PROJECT_ROOT, normalize_date, unique_path

PRODUCT_DETAIL_URL = "https://jdsz.jd.com/szweb/view/product/productDetail.html"


class ProductDetailDownloader:
    def __init__(self, browser: BrowserManager | None = None, db: Database | None = None):
        self.browser = browser or BrowserManager()
        self.db = db or Database()
        self._last_selected_date_range: tuple[str, str] | None = None

    async def _set_date(self, page: Page, date_start: str, date_end: str) -> None:
        logger.info("设置日期范围: %s ~ %s", date_start, date_end)
        await click_text(page, "自定义", timeout=5000)
        await page.wait_for_timeout(500)
        await select_calendar_range(page, date_start, date_end)
        self._last_selected_date_range = (date_start, date_end)
        await page.wait_for_timeout(1000)

    async def _selected_date_range(self, page: Page) -> tuple[str, str] | None:
        text = await page.locator("body").inner_text(timeout=5000)
        matches = list(re.finditer(
            r"当前\s*[:：]?\s*(20\d{2}-\d{2}-\d{2})(?:\s*(?:至|~|～)\s*(20\d{2}-\d{2}-\d{2}))?",
            text,
        ))
        if not matches:
            return None
        match = None
        for candidate in matches:
            after = text[candidate.end():candidate.end() + 12]
            if not re.match(r"\s+\d{2}:\d{2}", after):
                match = candidate
        if match is None:
            return None
        start = match.group(1)
        end = match.group(2) or start
        return start, end

    async def _assert_selected_date(self, page: Page, date_start: str, date_end: str) -> None:
        selected = None
        for _ in range(20):
            selected = await self._selected_date_range(page)
            if selected == (date_start, date_end):
                logger.info("页面当前日期校验通过: %s ~ %s", selected[0], selected[1])
                return
            await page.wait_for_timeout(500)
        if selected is None:
            if self._last_selected_date_range == (date_start, date_end):
                logger.warning(
                    "页面未暴露可读取的报表日期，使用刚设置的目标日期继续: %s ~ %s",
                    date_start,
                    date_end,
                )
                return
            raise AssertionError("未读取到页面右上角「当前」日期，停止下载")
        raise AssertionError(
            f"页面当前日期不匹配，停止下载: actual={selected[0]} ~ {selected[1]} "
            f"expected={date_start} ~ {date_end}"
        )

    async def _choose_download_options(self, page: Page) -> None:
        await click_text(page, "分天下载")
        await click_text(page, "不包含对比时间")
        await click_text(page, "确定")

    async def _visible_text_exists(self, page: Page, text: str, timeout: int = 3000) -> bool:
        try:
            target = page.get_by_text(text, exact=False)
            count = await target.count()
            deadline = datetime.now().timestamp() + timeout / 1000
            while datetime.now().timestamp() < deadline:
                for i in range(count):
                    try:
                        if await target.nth(i).is_visible(timeout=300):
                            return True
                    except Exception:
                        continue
                await page.wait_for_timeout(300)
                count = await target.count()
        except Exception:
            return False
        return False

    def _validate_source_name(self, source_name: str, date_start: str, date_end: str) -> None:
        if "实时" in source_name:
            raise AssertionError(
                f"下载结果疑似实时数据，已停止保存: 原始文件名={source_name} "
                f"目标日期={date_start}~{date_end}"
            )

    async def _download_latest_center_task(
        self,
        page: Page,
        save_path: Path,
        task_start_time: datetime,
    ) -> tuple[str, Path]:
        task_start_text = task_start_time.strftime("%Y-%m-%d %H:%M:%S")
        deadline = datetime.now().timestamp() + 300
        while datetime.now().timestamp() < deadline:
            await maybe_wait_captcha(page)
            await self._refresh_download_center(page)
            try:
                rows = page.locator("tr", has_text="已生成")
                count = await rows.count()
                candidates = []
                for i in range(count):
                    row = rows.nth(i)
                    text = await row.inner_text(timeout=1000)
                    created = self._extract_create_time(text)
                    if created and created >= task_start_time:
                        candidates.append((created, row))
                if candidates:
                    created, row = max(candidates, key=lambda item: item[0])
                    logger.info("匹配下载中心任务: created_at=%s task_start=%s", created.strftime("%Y-%m-%d %H:%M:%S"), task_start_text)
                    async with page.expect_download(timeout=30000) as download_info:
                        try:
                            await row.get_by_text("下载", exact=False).last.click()
                        except Exception:
                            await click_text(page, "下载")
                    download = await download_info.value
                    source_name = download.suggested_filename
                    await download.save_as(str(save_path))
                    return source_name, save_path
            except Exception as exc:
                logger.warning("下载中心轮询暂未成功: %s", exc)
            await page.wait_for_timeout(5000)
        raise TimeoutError(f"下载中心 5 分钟内未找到创建时间晚于 {task_start_text} 的已生成任务")

    async def _go_to_download_center(self, page: Page) -> Page:
        try:
            async with page.context.expect_page(timeout=15000) as page_info:
                await click_text(page, "前往查看", timeout=15000)
            center_page = await page_info.value
            await center_page.wait_for_load_state("domcontentloaded", timeout=30000)
            logger.info("已切换到新打开的下载中心标签页: %s", center_page.url)
            return center_page
        except Exception as exc:
            logger.warning("未捕获到新下载中心标签页，尝试在已有标签中查找: %s", exc)
            if await self._is_download_center_page(page):
                return page
            for candidate in reversed(page.context.pages):
                try:
                    if await self._is_download_center_page(candidate):
                        logger.info("已切换到已有下载中心标签页: %s", candidate.url)
                        return candidate
                except Exception:
                    continue
            raise TimeoutError("未找到下载中心标签页")

    async def _is_download_center_page(self, page: Page) -> bool:
        try:
            title = await page.title()
            if "下载中心" in title:
                return True
            text = await page.locator("body").inner_text(timeout=2000)
            return "下载中心" in text and ("已生成" in text or "生成中" in text or "创建时间" in text)
        except Exception:
            return False

    async def _refresh_download_center(self, page: Page) -> None:
        try:
            button = page.locator(
                "button, [role=button], .ant-btn, .el-button",
                has_text=re.compile(r"刷新|更新"),
            )
            count = await button.count()
            for i in range(count):
                item = button.nth(i)
                if await item.is_visible(timeout=300):
                    await item.click(timeout=3000)
                    logger.info("点击下载中心刷新按钮")
                    await page.wait_for_timeout(1500)
                    return
        except Exception as exc:
            logger.warning("下载中心刷新按钮点击失败，继续轮询: %s", exc)
        try:
            result = await page.evaluate(
                """
                () => {
                  const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden' && style.display !== 'none' &&
                      rect.width > 0 && rect.height > 0;
                  };
                  const all = Array.from(document.querySelectorAll('button, [role=button], i, svg, span'))
                    .filter(visible)
                    .map((el) => {
                      const rect = el.getBoundingClientRect();
                      const text = [
                        el.innerText,
                        el.textContent,
                        el.getAttribute('title'),
                        el.getAttribute('aria-label'),
                        el.getAttribute('class'),
                      ].filter(Boolean).join(' ');
                      return { text, x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                    })
                    .filter((item) => /refresh|reload|sync|刷新|更新/i.test(item.text))
                    .sort((a, b) => (b.x - a.x) || (a.y - b.y));
                  return all[0] || null;
                }
                """
            )
            if result:
                await page.mouse.click(result["x"] + result["width"] / 2, result["y"] + result["height"] / 2)
                logger.info("点击下载中心图标刷新按钮")
                await page.wait_for_timeout(1500)
                return
        except Exception as exc:
            logger.warning("下载中心图标刷新按钮点击失败，继续轮询: %s", exc)
        try:
            logger.info("未识别到下载中心刷新按钮，刷新当前下载中心页面")
            await page.reload(wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
        except Exception as exc:
            logger.warning("下载中心页面刷新失败，继续轮询: %s", exc)

    @staticmethod
    def _extract_create_time(text: str) -> datetime | None:
        match = re.search(r"(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", text)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    async def download(self, store: dict, tab: str, date_start: str, date_end: str) -> Path | None:
        date_start = normalize_date(date_start) or date_start
        date_end = normalize_date(date_end) or date_end
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
            await self._assert_selected_date(page, date_start, date_end)
            logger.info("点击下载数据前时间: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
            await click_text(page, "下载数据")
            save_dir = PROJECT_ROOT / "data" / "raw" / f"商品明细_{tab}" / date_end
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = unique_path(save_dir / f"商品明细_{tab}_{store.get('store_name')}_{date_start}_{date_end}.xlsx")
            if await self._visible_text_exists(page, "分天下载"):
                logger.info("检测到商品明细旧版下载弹窗，按下载中心流程处理")
                await self._choose_download_options(page)
                center_page = await self._go_to_download_center(page)
                source_name, saved_path = await self._download_latest_center_task(center_page, save_path, start_time)
            else:
                logger.info("检测到商品明细新版下载弹窗，点击确定后直接接收浏览器下载")
                async with page.expect_download(timeout=60000) as download_info:
                    await click_text(page, "确定", timeout=15000)
                download = await download_info.value
                source_name = download.suggested_filename
                self._validate_source_name(source_name, date_start, date_end)
                await download.save_as(str(save_path))
                saved_path = save_path
            logger.info("下载完成 原始文件名=%s 重命名后=%s 保存路径=%s", source_name, saved_path.name, saved_path)
            self.db.upsert_report_file(
                store_id=store.get("store_id"), report_type=task_name, date_start=date_start, date_end=date_end,
                source_file_name=source_name, saved_file_path=str(saved_path),
                download_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status="success", error_msg="",
            )
            return saved_path
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
