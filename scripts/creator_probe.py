"""Manual, read-only cloud probe. Never logs credentials or private messages."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.browser import _normalize_cookies
from app.config import parse_auth_json
from playwright.async_api import async_playwright

URL = "https://creator.douyin.com/creator-micro/data/following/chat"

async def main():
    secret = os.environ.get("DOUYIN_COOKIE", "")
    if not secret:
        print("Missing DOUYIN_COOKIE")
        return 1
    try:
        auth = parse_auth_json(secret, "DOUYIN_COOKIE")
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="chrome", headless=True)
            kwargs = {"locale": "zh-CN", "viewport": {"width": 1440, "height": 1000}}
            if isinstance(auth, dict):
                kwargs["storage_state"] = auth
            elif not isinstance(auth, list):
                print("Invalid auth format")
                return 1
            context = await browser.new_context(**kwargs)
            if isinstance(auth, list):
                await context.add_cookies(_normalize_cookies(auth))
            page = await context.new_page()
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            for _ in range(30):
                if await page.locator('[class*="item-header-name-"]:visible').count():
                    print("Creator message list is visible; no message was sent.")
                    await browser.close()
                    return 0
                for marker in ("安全验证", "完成验证", "验证身份", "扫码登录"):
                    item = page.get_by_text(marker, exact=True)
                    if await item.count() and await item.first.is_visible():
                        print("Login or security verification is required; stopped.")
                        await browser.close()
                        return 1
                await page.wait_for_timeout(1000)
            print("Creator message list did not appear; no message was sent.")
            await browser.close()
            return 1
    except Exception:
        print("Creator probe failed without exposing account information.")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
