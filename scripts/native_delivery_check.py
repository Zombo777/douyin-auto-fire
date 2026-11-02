"""User-authorized, single-recipient native emoji test; never retries a send."""
import asyncio, json, os, sys, hashlib
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import parse_auth_json
from app.browser import _normalize_cookies
from playwright.async_api import async_playwright
phase='initializing'

async def main():
    global phase
    state=parse_auth_json(os.environ.get('DOUYIN_COOKIE',''),'DOUYIN_COOKIE')
    target=os.environ.get('DOUYIN_TEST_FRIEND','').strip()
    if not target: raise RuntimeError('missing test friend')
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True)
        if isinstance(state,dict):
            context=await browser.new_context(storage_state=state,locale='zh-CN')
        elif isinstance(state,list):
            context=await browser.new_context(locale='zh-CN')
            await context.add_cookies(_normalize_cookies(state))
        else: raise RuntimeError('invalid auth')
        try:
            phase='open_page'
            page=await context.new_page()
            await page.goto('https://creator.douyin.com/creator-micro/data/following/chat',wait_until='domcontentloaded',timeout=60000)
            await page.locator('[class*="item-header-name-"]').first.wait_for(state='visible',timeout=45000)
            phase='friends_tab'
            tab=page.get_by_text('朋友私信',exact=True)
            if await tab.count()!=1 or not await tab.is_visible():raise RuntimeError('friends tab unavailable')
            await tab.click();await page.wait_for_timeout(3000)
            phase='find_target'
            row=page.locator('li:visible').filter(has=page.get_by_text(target,exact=True))
            stalls=0
            for _ in range(160):
                if await row.count():break
                moved=await page.evaluate('''() => {let box=[...document.querySelectorAll('#sub-app [role="grid"],#sub-app .ReactVirtualized__Grid')].find(x=>x.clientHeight>0&&x.scrollHeight>x.clientHeight);if(!box)return false;let old=box.scrollTop;box.scrollTop+=box.clientHeight*0.6;return box.scrollTop>old;}''')
                stalls=0 if moved else stalls+1
                if stalls>=20:break
                await page.wait_for_timeout(400)
            if await row.count()!=1:raise RuntimeError('target ambiguous')
            phase='open_target'
            await row.click()
            editor=page.locator('[contenteditable="true"]:visible')
            await editor.first.wait_for(state='visible',timeout=15000)
            header=page.locator('[class*="box-header-name-"]:visible')
            print(json.dumps({'editors':await editor.count(),'headers':await header.count(),'header_matches':await header.count()==1 and (await header.inner_text()).strip()==target}),flush=True)
            if await editor.count()!=1 or await header.count()!=1 or (await header.inner_text()).strip()!=target:raise RuntimeError('target verification failed')
            await page.wait_for_timeout(10000)
            result=await page.evaluate('''() => {
              let outgoing=[...document.querySelectorAll('[class*="is-me-"]')];
              let last=outgoing.at(-1);
              if(!last)return {outgoing_visible:outgoing.length};
              let fail=[...last.querySelectorAll('*')].some(e=>['class','aria-label','title'].some(a=>/(fail|retry|失败|重试)/i.test(e.getAttribute(a)||'')));
              return {outgoing_visible:outgoing.length,last_image_count:last.querySelectorAll('img').length,last_failure_marker:fail};
            }''')
            print(json.dumps(result))
        finally:await browser.close()

try:
    asyncio.run(main())
except Exception:
    print('Read-only delivery check failed.')
    sys.exit(1)
