from __future__ import annotations

import asyncio
import argparse
import base64
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import async_playwright


DOUYIN_URL = "https://www.douyin.com/"

async def login(output: Path, copy_to_clipboard: bool = False) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False, channel=os.getenv("BROWSER_CHANNEL", "chrome"))
        context = await browser.new_context(locale="zh-CN")
        page = await context.new_page()
        await page.goto(DOUYIN_URL, wait_until="domcontentloaded")
        await _open_login(page)
        print("请在浏览器中扫码登录。登录完成并看到抖音首页后，回到终端按 Enter。")
        await asyncio.to_thread(input)
        await page.goto(DOUYIN_URL, wait_until="domcontentloaded")
        await _verify_home_login(page)
        # Cloud dry-run validates chat separately from local state export.
        # No IndexedDB, browser profile, passwords, history, screenshots or trace.
        state = await context.storage_state()
        raw = json.dumps(state, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        payload = 'gzip64:' + base64.b64encode(gzip.compress(raw, mtime=0)).decode('ascii')
        if len(payload.encode('utf-8')) > 48 * 1024:
            raise RuntimeError("登录状态超过 GitHub Secret 大小限制，未保存或复制")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + '.tmp')
        temporary.write_text(payload, encoding='utf-8')
        temporary.replace(output)
        if copy_to_clipboard:
            if sys.platform != 'win32':
                raise RuntimeError('自动复制当前仅支持 Windows')
            subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                            'Set-Clipboard -Value ([Console]::In.ReadToEnd())'],
                           input=payload, text=True, encoding='utf-8', check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await browser.close()
        print("登录状态已安全保存。" + ("已复制到剪贴板，请直接粘贴到 GitHub Secret。" if copy_to_clipboard else ""))


async def _open_login(page) -> None:
    login = page.get_by_text("登录", exact=True)
    if await login.count():
        try:
            await login.first.click(timeout=10_000)
        except Exception:
            pass

    qr_login = page.get_by_text("扫码登录", exact=True)
    if await qr_login.count():
        try:
            await qr_login.first.click(timeout=5_000)
        except Exception:
            pass


async def _verify_home_login(page) -> None:
    login = page.get_by_text("登录", exact=True)
    if await login.count() and await login.first.is_visible():
        raise RuntimeError("未检测到登录成功，请重新运行并完成扫码确认")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('storage-state.json'))
    parser.add_argument('--copy', action='store_true')
    args = parser.parse_args()
    asyncio.run(login(args.output, args.copy))

