"""Read-only native emoji readiness test; never sends or logs private data."""
import asyncio, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import parse_auth_json
from app.browser import _normalize_cookies
from playwright.async_api import async_playwright

async def main():
    auth=parse_auth_json(os.environ.get('DOUYIN_COOKIE',''),'DOUYIN_COOKIE')
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True)
        if isinstance(auth,dict):
            context=await browser.new_context(storage_state=auth,locale='zh-CN')
        elif isinstance(auth,list):
            context=await browser.new_context(locale='zh-CN')
            await context.add_cookies(_normalize_cookies(auth))
        else: raise ValueError('invalid auth')
        try:
            page=await context.new_page()
            await page.goto('https://creator.douyin.com/creator-micro/data/following/chat',wait_until='domcontentloaded',timeout=60000)
            await page.locator('[class*="item-header-name-"]').first.wait_for(state='visible',timeout=45000)
            tab=page.get_by_text('朋友私信',exact=True)
            if await tab.count()!=1 or not await tab.is_visible():raise RuntimeError('friends tab unavailable')
            await tab.click();await page.wait_for_timeout(4000)
            row=page.locator('li:visible:has([class*="item-header-name-"])').first
            await row.wait_for(state='visible',timeout=30000)
            await row.click()
            await page.locator('[contenteditable="true"]:visible').first.wait_for(state='visible',timeout=30000)
            await page.wait_for_timeout(3000)
            control=page.locator('img[class*="chat-sticker"]:visible')
            if await control.count()!=1:raise RuntimeError('emoji control unavailable')
            await control.click();await page.wait_for_timeout(7000)
            panel=page.locator('[class*="chat-emoji-"]:visible')
            if await panel.count()!=1:raise RuntimeError('panel unavailable')
            imgs=panel.locator('img')
            if await imgs.count()<23:raise RuntimeError('emoji unavailable')
            valid=await imgs.nth(22).evaluate('''e=>{let p=e.closest('[class*="chat-emoji-"]'),a=e.getBoundingClientRect(),b=p.getBoundingClientRect();let x=a.left+a.width/2-b.left,y=a.top+a.height/2-b.top;return e.naturalWidth>0&&x>115&&x<155&&y>125&&y<165}''')
            if not valid:raise RuntimeError('emoji position differs')
            print(json.dumps({'native_emoji_ready':True,'sent':False}))
        finally:await browser.close()

try:
    asyncio.run(main())
except Exception:
    print('Native emoji dry run failed; no message was sent.')
    sys.exit(1)
