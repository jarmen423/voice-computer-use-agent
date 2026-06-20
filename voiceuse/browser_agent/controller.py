"""Playwright-based browser controller.

Supports both attaching to an existing Chrome via the Chrome DevTools Protocol
(CDP) and launching a fresh Chrome instance. When launching, the user's real
Chrome profile can be reused by pointing ``profile_dir`` at the Chrome user data
directory (e.g. ``~/.config/google-chrome``).
"""

from __future__ import annotations

import base64
import logging
import random
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from voiceuse.config import BrowserAgentConfig

logger = logging.getLogger(__name__)


class BrowserController:
    """High-level async browser automation wrapper around Playwright."""

    def __init__(self, config: BrowserAgentConfig) -> None:
        self.config = config
        self._playwright: Any = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def launch(self) -> None:
        """Launch a new Chrome instance and open a page."""
        if self._browser is not None:
            logger.info("Browser already connected; reusing existing connection.")
            return

        self._playwright = await async_playwright().start()
        args = [
            f"--remote-debugging-port={self.config.cdp_port}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        launch_kwargs: dict[str, Any] = {
            "headless": self.config.headless,
            "args": args,
        }

        # Prefer the system Google Chrome installation. Playwright does not ship
        # a Chromium binary for every Ubuntu version, but it can drive an
        # already-installed Chrome via the "chrome" channel.
        if Path(self.config.chrome_path).exists():
            launch_kwargs["executable_path"] = self.config.chrome_path
        else:
            launch_kwargs["channel"] = "chrome"

        if self.config.profile_dir:
            profile = Path(self.config.profile_dir).expanduser()
            launch_kwargs["user_data_dir"] = str(profile)

        logger.info("Launching Chrome from %s with profile %s", self.config.chrome_path, self.config.profile_dir)
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            no_viewport=False,
        )
        self._page = await self._context.new_page()
        await self._page.goto("about:blank")

    async def attach(self) -> None:
        """Attach to an existing Chrome via CDP."""
        if self._browser is not None:
            logger.info("Browser already connected; reusing existing connection.")
            return

        cdp_url = f"http://127.0.0.1:{self.config.cdp_port}"
        self._playwright = await async_playwright().start()
        logger.info("Attaching to Chrome at %s", cdp_url)
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = await self._browser.new_context()
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

    async def close(self) -> None:
        """Disconnect from the browser."""
        try:
            if self._browser:
                await self._browser.close()
        except Exception as exc:
            logger.warning("Error closing browser: %s", exc)
        finally:
            self._browser = None
            self._context = None
            self._page = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None

    def is_connected(self) -> bool:
        """Return True when a browser page is available."""
        return self._page is not None and not self._page.is_closed()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def goto(self, url: str) -> str:
        """Navigate the current page to *url*."""
        page = self._require_page()
        url = url.strip()
        if not url:
            raise RuntimeError("Empty URL.")
        # Only add https:// for bare hostnames/domains, not for about:/file:/data: URLs.
        if not any(url.startswith(p) for p in ("http://", "https://", "about:", "file:", "data:")):
            url = "https://" + url
        await page.goto(url, wait_until="domcontentloaded")
        await self._settle()
        return f"Navigated to {page.url}"

    async def click(self, target: str) -> str:
        """Click an element by selector or visible text."""
        page = self._require_page()
        locator = await self._resolve_locator(page, target)
        await locator.scroll_into_view_if_needed()
        await locator.click()
        await self._settle()
        return f"Clicked {target}"

    async def type_text(self, target: str, text: str) -> str:
        """Click a field and type *text* into it.

        If *target* cannot be resolved or resolves to a non-text input element,
        fall back to the first visible text-like input on the page.
        """
        page = self._require_page()

        async def _is_text_field(loc: Any) -> bool:
            try:
                tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                input_type = await loc.evaluate("el => (el.type || '').toLowerCase()")
                return tag in ("textarea",) or (tag == "input" and input_type in ("text", "search", "email", "url", "password", "tel", "number", ""))
            except Exception:
                return False

        async def _first_text_input() -> Any:
            for selector in [
                "input[type='search']:visible",
                "input[type='text']:visible",
                "textarea:visible",
                "input:visible",
            ]:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await _is_text_field(loc):
                    return loc
            return None

        locator = None
        try:
            locator = await self._resolve_locator(page, target)
        except Exception:
            pass

        if locator is None or not await _is_text_field(locator):
            fallback = await _first_text_input()
            if fallback is None:
                raise RuntimeError(f"Could not find a text input for typing. Target was: {target}")
            locator = fallback
            target = "first visible text input"

        await locator.click()
        await locator.fill(text)
        await self._settle()
        return f"Typed {len(text)} characters into {target}"

    async def press(self, key: str) -> str:
        """Press a single key such as Enter, Tab, Escape, ArrowDown."""
        page = self._require_page()
        await page.keyboard.press(key)
        await self._settle()
        return f"Pressed {key}"

    async def scroll(self, direction: str = "down", amount: int = 400) -> str:
        """Scroll the page."""
        page = self._require_page()
        if direction.lower() in ("down", "up"):
            delta = amount if direction.lower() == "down" else -amount
            await page.mouse.wheel(0, delta)
        elif direction.lower() in ("right", "left"):
            delta = amount if direction.lower() == "right" else -amount
            await page.mouse.wheel(delta, 0)
        await self._settle()
        return f"Scrolled {direction} by {amount}px"

    async def wait(self, seconds: float = 1.0) -> str:
        """Wait briefly."""
        seconds = max(0.0, min(float(seconds), 10.0))
        await self._require_page().wait_for_timeout(seconds * 1000)
        return f"Waited {seconds:.1f} seconds"

    async def screenshot(self) -> str:
        """Return a base64-encoded PNG screenshot of the current viewport."""
        page = self._require_page()
        png_bytes = await page.screenshot(
            full_page=False,
            type="png",
            scale="css",  # keeps file size reasonable
        )
        return base64.b64encode(png_bytes).decode("ascii")

    async def get_page_info(self) -> dict[str, Any]:
        """Return URL, title, and a compact list of interactive elements."""
        page = self._require_page()
        url = page.url
        title = await page.title()
        elements = await page.evaluate(
            """() => {
                const out = [];
                const tags = ['a', 'button', 'input', 'textarea', 'select'];
                for (const tag of tags) {
                    for (const el of document.querySelectorAll(tag)) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) continue;
                        const style = window.getComputedStyle(el);
                        if (style.visibility === 'hidden' || style.display === 'none') continue;
                        const labelParts = [
                            el.innerText,
                            el.value,
                            el.placeholder,
                            el.getAttribute('aria-label'),
                            el.getAttribute('title'),
                            el.getAttribute('name'),
                            el.id
                        ];
                        const text = labelParts.filter(Boolean).join(' | ').slice(0, 120);
                        const selector = el.id
                            ? '#' + el.id
                            : (el.name
                                ? tag + '[name="' + el.name + '"]'
                                : tag + (el.className ? '.' + el.className.split(' ').slice(0, 2).join('.') : ''));
                        out.push({
                            tag,
                            text,
                            type: el.type || null,
                            selector
                        });
                    }
                }
                return out.slice(0, 40);
            }"""
        )
        return {"url": url, "title": title, "elements": elements}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_page(self) -> Page:
        if self._page is None or self._page.is_closed():
            raise RuntimeError("Browser is not connected. Launch or attach first.")
        return self._page

    async def _resolve_locator(self, page: Page, target: str) -> Any:
        """Resolve *target* into a Playwright locator.

        First try interpreting it as a CSS selector. If that fails, fall back to
        a text-based locator.
        """
        # Try CSS selector first
        try:
            locator = page.locator(target)
            count = await locator.count()
            if count > 0:
                return locator.first
        except Exception:
            pass

        # Fall back to text-based locator
        locator = page.get_by_text(target, exact=False)
        count = await locator.count()
        if count > 0:
            return locator.first

        # Try role-based fallback for buttons/links
        locator = page.locator(f"button:has-text('{target}'), a:has-text('{target}'), [placeholder='{target}']")
        count = await locator.count()
        if count > 0:
            return locator.first

        raise RuntimeError(f"Could not find element matching: {target}")

    async def _settle(self) -> None:
        """Small human-like delay plus wait for network to be idle-ish."""
        try:
            await self._require_page().wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        await self._require_page().wait_for_timeout(random.randint(200, 400))
