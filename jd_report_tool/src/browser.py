from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from .logger import logger
from .utils import PROJECT_ROOT

USER_DATA_DIR = Path(os.environ.get("JD_CHROME_USER_DATA", PROJECT_ROOT / "data" / "chrome_user_data"))


class BrowserManager:
    """Playwright 持久化 Chrome 上下文，复用本机/本工具登录状态且不保存账号密码。"""

    def __init__(self, headless: bool = False, user_data_dir: Path = USER_DATA_DIR):
        self.headless = headless
        self.user_data_dir = Path(user_data_dir)
        self._playwright: Any = None
        self.context: BrowserContext | None = None

    async def start(self) -> BrowserContext:
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

    async def new_page(self) -> Page:
        if not self.context:
            await self.start()
        assert self.context
        page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        page.set_default_timeout(15000)
        return page

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self._playwright:
            await self._playwright.stop()


def run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return loop.run_until_complete(coro)


async def click_text(page: Page, text: str, fallback: str | None = None, timeout: int = 10000) -> None:
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        logger.info("点击文本: %s", text)
        return
    except Exception as exc:
        logger.warning("文本点击失败: %s, %s", text, exc)
    if fallback:
        await page.locator(fallback).first.click(timeout=timeout)
        logger.info("点击备用选择器: %s", fallback)


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
