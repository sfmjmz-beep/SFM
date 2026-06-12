from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .logger import logger
from .utils import PROJECT_ROOT

USER_DATA_DIR = Path(os.environ.get("JD_CHROME_USER_DATA", PROJECT_ROOT / "data" / "chrome_user_data"))
DEFAULT_CHROME_DEBUG_PORT = int(os.environ.get("JD_CHROME_DEBUG_PORT", "9222"))
DEFAULT_CHROME_USER_DATA_DIR = Path(
    os.environ.get(
        "JD_DEFAULT_CHROME_USER_DATA",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
    )
)
DEFAULT_CHROME_PROFILE = os.environ.get("JD_DEFAULT_CHROME_PROFILE", "Default")


def _safe_profile_part(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text)
    return text.strip("._") or "store"


def store_user_data_dir(store: dict[str, Any]) -> Path:
    """Return an isolated browser profile directory for one store."""
    store_id = _safe_profile_part(store.get("store_id"))
    store_name = _safe_profile_part(store.get("store_name"))
    return USER_DATA_DIR / "stores" / f"{store_id}_{store_name}"


def default_chrome_executable() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for path in candidates:
        if path.exists():
            return path
    return Path("chrome.exe")


def cdp_available(port: int = DEFAULT_CHROME_DEBUG_PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1):
            return True
    except (OSError, urllib.error.URLError):
        return False


class BrowserManager:
    """Playwright 持久化 Chrome 上下文，复用本机/本工具登录状态且不保存账号密码。"""

    def __init__(
        self,
        headless: bool = False,
        user_data_dir: Path = USER_DATA_DIR,
        use_default_browser: bool = False,
    ):
        self.headless = headless
        self.user_data_dir = Path(user_data_dir)
        self.use_default_browser = use_default_browser
        self._playwright: Any = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def start(self) -> BrowserContext:
        if self.use_default_browser:
            return await self._start_default_browser()

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        channel = "chrome"
        try:
            self.context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                channel=channel,
                headless=self.headless,
                accept_downloads=True,
                args=["--start-maximized"],
                viewport=None,
            )
        except Exception as exc:
            logger.warning("启动本机 Chrome 失败，改用 Playwright Chromium: %s", exc)
            self.context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir), headless=self.headless, accept_downloads=True, viewport=None
            )
        logger.info("浏览器已打开，用户数据目录: %s", self.user_data_dir)
        return self.context

    async def _start_default_browser(self) -> BrowserContext:
        if self.headless:
            raise RuntimeError("默认浏览器模式不支持无头运行")

        self._playwright = await async_playwright().start()
        if not cdp_available():
            chrome = default_chrome_executable()
            DEFAULT_CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            args = [
                str(chrome),
                f"--remote-debugging-port={DEFAULT_CHROME_DEBUG_PORT}",
                f"--user-data-dir={DEFAULT_CHROME_USER_DATA_DIR}",
                f"--profile-directory={DEFAULT_CHROME_PROFILE}",
                "--start-maximized",
            ]
            logger.info("启动默认 Chrome 调试模式: %s", " ".join(args))
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + 12
            while time.time() < deadline and not cdp_available():
                time.sleep(0.5)

        if not cdp_available():
            await self._playwright.stop()
            self._playwright = None
            raise RuntimeError(
                "无法连接默认 Chrome。请先关闭所有 Chrome 窗口，再点击“登录/打开浏览器”，"
                "让工具用默认 Chrome 资料重新打开浏览器后再下载。"
            )

        self.browser = await self._playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{DEFAULT_CHROME_DEBUG_PORT}")
        if not self.browser.contexts:
            raise RuntimeError("已连接默认 Chrome，但没有可用浏览器上下文")
        self.context = self.browser.contexts[0]
        logger.info("已连接默认 Chrome: user_data=%s profile=%s port=%s", DEFAULT_CHROME_USER_DATA_DIR, DEFAULT_CHROME_PROFILE, DEFAULT_CHROME_DEBUG_PORT)
        return self.context

    async def new_page(self) -> Page:
        if not self.context:
            await self.start()
        assert self.context
        page = await self.context.new_page()
        page.set_default_timeout(15000)
        return page

    async def close(self) -> None:
        if self.context and not self.use_default_browser:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
        self.context = None
        self.browser = None
        self._playwright = None


def run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return loop.run_until_complete(coro)


async def click_text(page: Page, text: str, fallback: str | None = None, timeout: int = 10000) -> None:
    try:
        target = page.get_by_text(text, exact=False)
        count = await target.count()
        for i in range(count):
            item = target.nth(i)
            try:
                if await item.is_visible(timeout=500):
                    await item.click(timeout=timeout)
                    logger.info("点击文本: %s", text)
                    return
            except Exception:
                continue
        await target.first.click(timeout=timeout)
        logger.info("点击文本: %s", text)
        return
    except Exception as exc:
        logger.warning("文本点击失败: %s, %s", text, exc)
    if fallback:
        await page.locator(fallback).first.click(timeout=timeout)
        logger.info("点击备用选择器: %s", fallback)
        return
    raise TimeoutError(f"未找到可点击文本: {text}")


async def click_calendar_date(page: Page, date_text: str, double_click: bool = False) -> None:
    """Click a visible date cell in JD calendar widgets by YYYY-MM-DD."""
    target = datetime.strptime(date_text, "%Y-%m-%d")
    month_label = f"{target.year}年{target.month:02d}月"
    day_label = str(target.day)
    result = await page.evaluate(
        """
        ({ monthLabel, dayLabel }) => {
          const visible = (el) => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style && style.visibility !== 'hidden' && style.display !== 'none' &&
              rect.width > 0 && rect.height > 0;
          };
          const box = (el) => {
            const rect = el.getBoundingClientRect();
            return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
          };
          const all = Array.from(document.querySelectorAll('body *')).filter(visible);
          const headers = all
            .filter((el) => (el.innerText || el.textContent || '').trim() === monthLabel)
            .map((el) => ({ el, rect: box(el) }));
          const candidates = all
            .filter((el) => {
              const text = (el.innerText || el.textContent || '').trim();
              const cls = String(el.className || '').toLowerCase();
              const disabled = el.getAttribute('disabled') !== null ||
                el.getAttribute('aria-disabled') === 'true' ||
                cls.includes('disabled');
              return text === dayLabel && !disabled;
            })
            .map((el) => ({ el, rect: box(el) }));
          if (!candidates.length) return null;
          let best = null;
          let bestScore = Number.POSITIVE_INFINITY;
          for (const candidate of candidates) {
            for (const header of headers.length ? headers : [{ rect: { x: 0, y: 0, width: window.innerWidth, height: 0 } }]) {
              if (candidate.rect.y < header.rect.y) continue;
              const headerCenter = header.rect.x + header.rect.width / 2;
              const candidateCenter = candidate.rect.x + candidate.rect.width / 2;
              const dx = Math.abs(candidateCenter - headerCenter);
              const dy = candidate.rect.y - header.rect.y;
              const score = dx + dy / 4;
              if (score < bestScore) {
                best = candidate.rect;
                bestScore = score;
              }
            }
          }
          return best || candidates[0].rect;
        }
        """,
        {"monthLabel": month_label, "dayLabel": day_label},
    )
    if not result:
        raise TimeoutError(f"未找到日历日期: {date_text}")
    x = result["x"] + result["width"] / 2
    y = result["y"] + result["height"] / 2
    if double_click:
        await page.mouse.dblclick(x, y)
    else:
        await page.mouse.click(x, y)
    logger.info("点击日历日期: %s", date_text)


async def select_calendar_range(page: Page, date_start: str, date_end: str) -> None:
    await click_calendar_date(page, date_start)
    await page.wait_for_timeout(300)
    await click_calendar_date(page, date_end, double_click=True)


async def maybe_wait_captcha(page: Page) -> None:
    content = await page.content()
    if any(k in content for k in ["验证码", "安全验证", "滑块"]):
        logger.warning("检测到验证码/安全验证，请在浏览器中手动处理，处理完成后程序将继续等待页面稳定")
        await page.wait_for_timeout(30000)


async def screenshot_error(page: Page, task_name: str) -> Path:
    folder = PROJECT_ROOT / "logs" / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{task_name}_{asyncio.get_event_loop().time():.0f}.png"
    await page.screenshot(path=str(path), full_page=True)
    logger.info("失败截图保存: %s", path)
    return path
