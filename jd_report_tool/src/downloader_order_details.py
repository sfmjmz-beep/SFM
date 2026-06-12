from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .browser import BrowserManager, click_calendar_date, click_text, maybe_wait_captcha, screenshot_error
from .database import Database
from .logger import logger
from .utils import PROJECT_ROOT, normalize_date, unique_path

ORDER_DETAILS_URL = "https://jdsz.jd.com/szweb/view/tradeAnalysis/orderDetails.html"


@dataclass(frozen=True)
class OrderReportSpec:
    report_type: str
    report_name: str
    tab_name: str


ORDER_REPORTS: dict[str, OrderReportSpec] = {
    "trade_order_success": OrderReportSpec("trade_order_success", "成交订单", "成交订单"),
    "trade_order_cancel": OrderReportSpec("trade_order_cancel", "取消订单", "取消订单"),
    "trade_order_refund": OrderReportSpec("trade_order_refund", "售后退款订单", "售后退款订单"),
}


class OrderDetailsDownloader:
    def __init__(self, browser: BrowserManager | None = None, db: Database | None = None):
        self.browser = browser or BrowserManager()
        self.db = db or Database()

    async def open_page(self) -> Page:
        page = await self.browser.new_page()
        await page.goto(ORDER_DETAILS_URL, wait_until="domcontentloaded", timeout=60000)
        await maybe_wait_captcha(page)
        await page.wait_for_load_state("networkidle", timeout=30000)
        text = await page.locator("body").inner_text(timeout=10000)
        if "登录" in text and "京东商智" not in text:
            raise RuntimeError("页面可能登录失效，请先在工具浏览器中重新登录京东商智")
        return page

    async def select_custom_date(self, page: Page, start_date: str, end_date: str) -> None:
        logger.info("订单明细设置自定义日期: %s ~ %s", start_date, end_date)
        if await self._read_current_date(page) == (start_date, end_date):
            logger.info("订单明细页面当前日期已是目标日期: %s ~ %s", start_date, end_date)
            return
        await self._open_custom_calendar(page, start_date)
        await self._select_order_date_range(page, start_date, end_date)
        await page.wait_for_timeout(1000)

    async def _open_custom_calendar(self, page: Page, target_date: str) -> None:
        for attempt in range(3):
            await self._click_custom_date(page)
            await page.wait_for_timeout(600)
            if await self._calendar_has_date(page, target_date):
                return
            logger.warning("订单明细自定义日历未展开，重试: attempt=%s target=%s", attempt + 1, target_date)
        raise TimeoutError(f"订单明细自定义日历未展开或未找到日期: {target_date}")

    async def _calendar_has_date(self, page: Page, date_text: str) -> bool:
        try:
            target = datetime.strptime(date_text, "%Y-%m-%d")
            month_label = f"{target.year}年{target.month:02d}月"
            day_label = str(target.day)
            return bool(
                await page.evaluate(
                    """
                    ({ monthLabel, dayLabel }) => {
                      const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style && style.visibility !== 'hidden' && style.display !== 'none' &&
                          rect.width > 0 && rect.height > 0;
                      };
                      const all = Array.from(document.querySelectorAll('body *')).filter(visible);
                      const hasMonth = all.some((el) => (el.innerText || el.textContent || '').trim() === monthLabel);
                      const hasDay = all.some((el) => (el.innerText || el.textContent || '').trim() === dayLabel);
                      return hasMonth && hasDay;
                    }
                    """,
                    {"monthLabel": month_label, "dayLabel": day_label},
                )
            )
        except Exception:
            return False

    async def _select_order_date_range(self, page: Page, start_date: str, end_date: str) -> None:
        await click_calendar_date(page, start_date)
        await page.wait_for_timeout(300)
        if not await self._calendar_has_date(page, end_date):
            await self._open_custom_calendar(page, end_date)
        await click_calendar_date(page, end_date, double_click=True)
        logger.info("订单明细日期范围已选择: %s ~ %s", start_date, end_date)

    async def _click_custom_date(self, page: Page) -> None:
        result = await page.evaluate(
            """
            () => {
              const visible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none' &&
                  rect.width > 0 && rect.height > 0;
              };
              const all = Array.from(document.querySelectorAll('button, span, div')).filter(visible);
              const candidates = all
                .filter((el) => (el.innerText || el.textContent || '').trim() === '自定义')
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
            raise TimeoutError("未找到订单明细自定义日期按钮")
        await page.mouse.click(result["x"] + result["width"] / 2, result["y"] + result["height"] / 2)
        logger.info("点击订单明细自定义日期按钮")

    async def verify_current_date(self, page: Page, start_date: str, end_date: str) -> None:
        selected = None
        for _ in range(20):
            selected = await self._read_current_date(page)
            if selected == (start_date, end_date):
                logger.info("订单明细页面日期校验通过: %s ~ %s", start_date, end_date)
                return
            await page.wait_for_timeout(500)
        if selected is None:
            raise AssertionError("未读取到订单明细右上角「当前」日期")
        raise AssertionError(
            f"订单明细页面日期不匹配: actual={selected[0]} ~ {selected[1]} expected={start_date} ~ {end_date}"
        )

    async def _read_current_date(self, page: Page) -> tuple[str, str] | None:
        text = await page.locator("body").inner_text(timeout=5000)
        matches = list(
            re.finditer(
                r"当前\s*[:：]?\s*(20\d{2}-\d{2}-\d{2})\s*(?:~|～|至|-)\s*(20\d{2}-\d{2}-\d{2})",
                text,
            )
        )
        if not matches:
            single = list(re.finditer(r"当前\s*[:：]?\s*(20\d{2}-\d{2}-\d{2})", text))
            if not single:
                return None
            day = single[-1].group(1)
            return day, day
        match = matches[-1]
        return match.group(1), match.group(2)

    async def select_tab(self, page: Page, tab_name: str) -> None:
        result = await page.evaluate(
            """
            (tabName) => {
              const visible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none' &&
                  rect.width > 0 && rect.height > 0;
              };
              const all = Array.from(document.querySelectorAll('button, a, span, div')).filter(visible);
              const candidates = all
                .filter((el) => (el.innerText || el.textContent || '').trim() === tabName)
                .map((el) => {
                  const rect = el.getBoundingClientRect();
                  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                })
                .filter((rect) => rect.y > 120 && rect.y < 220)
                .sort((a, b) => a.y - b.y || a.x - b.x);
              return candidates[0] || null;
            }
            """,
            tab_name,
        )
        if not result:
            raise TimeoutError(f"未找到订单明细 Tab: {tab_name}")
        await page.mouse.click(result["x"] + result["width"] / 2, result["y"] + result["height"] / 2)
        logger.info("订单明细切换 Tab: %s", tab_name)
        await page.wait_for_timeout(800)

    async def click_query(self, page: Page) -> None:
        await click_text(page, "查询", timeout=10000)

    async def wait_table_loaded(self, page: Page) -> None:
        await page.wait_for_timeout(5000)
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            logger.warning("订单明细等待 networkidle 超时，继续检查页面状态")

    async def click_download(self, page: Page):
        async with page.expect_download(timeout=60000) as download_info:
            await click_text(page, "下载数据", timeout=10000)
        return await download_info.value

    async def wait_download_finished(self, page: Page):
        return await self.click_download(page)

    def rename_file(self, store_name: str, report_type: str, start_date: str, end_date: str) -> Path:
        download_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_store = re.sub(r'[\\/:*?"<>|]+', "_", store_name).strip() or "店铺"
        save_dir = PROJECT_ROOT / "data" / "raw" / "订单明细" / report_type / end_date
        save_dir.mkdir(parents=True, exist_ok=True)
        return unique_path(save_dir / f"{safe_store}_{report_type}_{start_date}_{end_date}_{download_time}.xlsx")

    def _validate_source_name(self, source_name: str, start_date: str, end_date: str) -> None:
        dates = re.findall(r"20\d{2}-\d{2}-\d{2}", source_name)
        if dates and start_date not in dates and end_date not in dates:
            raise AssertionError(
                f"下载文件名日期疑似不匹配: 原始文件名={source_name} 目标日期={start_date}~{end_date}"
            )

    async def _has_download_button(self, page: Page) -> bool:
        try:
            target = page.get_by_text("下载数据", exact=False)
            count = await target.count()
            for i in range(count):
                if await target.nth(i).is_visible(timeout=300):
                    return True
        except Exception:
            return False
        return False

    async def _has_no_data(self, page: Page) -> bool:
        text = await page.locator("body").inner_text(timeout=5000)
        return any(key in text for key in ["暂无数据", "暂无相关数据", "无数据"])

    def write_download_log(
        self,
        *,
        store: dict,
        spec: OrderReportSpec,
        start_date: str,
        end_date: str,
        status: str,
        file_path: str = "",
        file_name: str = "",
        error_message: str = "",
        screenshot_path: str = "",
    ) -> None:
        self.db.write_download_log(
            store_id=store.get("store_id"),
            store_name=store.get("store_name", ""),
            report_type=spec.report_type,
            report_name=spec.report_name,
            start_date=start_date,
            end_date=end_date,
            page_url=ORDER_DETAILS_URL,
            file_name=file_name,
            file_path=file_path,
            download_status=status,
            download_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            error_message=error_message,
            screenshot_path=screenshot_path,
        )

    async def download(self, store: dict, report_type: str, start_date: str, end_date: str) -> Path | None:
        if report_type not in ORDER_REPORTS:
            raise ValueError(f"未知订单明细报表类型: {report_type}")
        spec = ORDER_REPORTS[report_type]
        start_date = normalize_date(start_date) or start_date
        end_date = normalize_date(end_date) or end_date
        page: Page | None = None
        screenshot_path = ""
        logger.info(
            "任务名称=%s 店铺=%s 日期=%s~%s URL=%s",
            spec.report_name,
            store.get("store_name"),
            start_date,
            end_date,
            ORDER_DETAILS_URL,
        )
        try:
            page = await self.open_page()
            await self.select_tab(page, spec.tab_name)
            await self.select_custom_date(page, start_date, end_date)
            await self.verify_current_date(page, start_date, end_date)
            await self.click_query(page)
            await self.wait_table_loaded(page)
            await self.verify_current_date(page, start_date, end_date)

            if not await self._has_download_button(page):
                if await self._has_no_data(page):
                    logger.info("订单明细无数据: %s %s~%s", spec.report_name, start_date, end_date)
                    self.write_download_log(
                        store=store,
                        spec=spec,
                        start_date=start_date,
                        end_date=end_date,
                        status="no_data",
                        error_message="页面显示暂无数据，且没有可用下载按钮",
                    )
                    return None
                raise TimeoutError("未找到订单明细下载数据按钮")

            save_path = self.rename_file(store.get("store_name", ""), spec.report_type, start_date, end_date)
            try:
                download = await self.wait_download_finished(page)
            except PlaywrightTimeoutError as exc:
                raise TimeoutError(f"订单明细下载超时: {exc}") from exc
            source_name = download.suggested_filename
            self._validate_source_name(source_name, start_date, end_date)
            await download.save_as(str(save_path))
            logger.info("订单明细下载完成 原始文件名=%s 保存路径=%s", source_name, save_path)
            self.write_download_log(
                store=store,
                spec=spec,
                start_date=start_date,
                end_date=end_date,
                status="success",
                file_path=str(save_path),
                file_name=source_name,
            )
            return save_path
        except AssertionError as exc:
            status = "date_mismatch" if "日期" in str(exc) else "failed"
            if page is not None:
                try:
                    screenshot_path = str(await screenshot_error(page, spec.report_name))
                except Exception:
                    pass
            logger.exception("订单明细下载失败: %s", exc)
            self.write_download_log(
                store=store,
                spec=spec,
                start_date=start_date,
                end_date=end_date,
                status=status,
                error_message=str(exc),
                screenshot_path=screenshot_path,
            )
            return None
        except TimeoutError as exc:
            if page is not None:
                try:
                    screenshot_path = str(await screenshot_error(page, spec.report_name))
                except Exception:
                    pass
            logger.exception("订单明细下载超时/失败: %s", exc)
            self.write_download_log(
                store=store,
                spec=spec,
                start_date=start_date,
                end_date=end_date,
                status="timeout" if "超时" in str(exc) else "failed",
                error_message=str(exc),
                screenshot_path=screenshot_path,
            )
            return None
        except Exception as exc:
            if page is not None:
                try:
                    screenshot_path = str(await screenshot_error(page, spec.report_name))
                except Exception:
                    pass
            logger.exception("订单明细下载失败: %s", exc)
            self.write_download_log(
                store=store,
                spec=spec,
                start_date=start_date,
                end_date=end_date,
                status="failed",
                error_message=str(exc),
                screenshot_path=screenshot_path,
            )
            return None
