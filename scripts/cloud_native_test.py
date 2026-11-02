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
            if (await editor.inner_text()).strip():raise RuntimeError('editor not empty')
            control=page.locator('img[class*="chat-sticker"]:visible')
            if await control.count()!=1:raise RuntimeError('sticker control missing')
            phase='open_emoji_panel'
            await control.click();await page.wait_for_timeout(7000)
            panel=page.locator('[class*="chat-emoji-"]:visible')
            if await panel.count()!=1:raise RuntimeError('panel missing')
            imgs=panel.locator('img')
            if await imgs.count()<23:raise RuntimeError('emoji missing')
            element=imgs.nth(22)
            valid=await element.evaluate('''e=>{let p=e.closest('[class*="chat-emoji-"]'),a=e.getBoundingClientRect(),b=p.getBoundingClientRect();let x=a.left+a.width/2-b.left,y=a.top+a.height/2-b.top;return e.naturalWidth>0&&x>115&&x<155&&y>125&&y<165}''')
            if not valid:raise RuntimeError('emoji position changed')
            src=await element.get_attribute('src')
            if not src or hashlib.sha256(urlsplit(src).path.encode()).hexdigest()!='7baa51bf16111187a321ca52397d920f224231a32a5dbb56bbb39501bf564737':raise RuntimeError('emoji identity changed')
            before=await page.locator('[class*="is-me-"]').count()
            phase='select_emoji'
            await element.click()
            await page.wait_for_timeout(4000)
            after=await page.locator('[class*="is-me-"]').count()
            contents=await editor.evaluate('(e)=>({text:!!e.innerText.trim(),images:e.querySelectorAll("img").length})')
            print(json.dumps({'clicked_once':True,'outgoing_delta':after-before,'editor_text':contents['text'],'editor_images':contents['images']}))
            if after != before or contents['images']!=1 or contents['text']:
                raise RuntimeError('ambiguous emoji state; not submitting')
            if (await header.inner_text()).strip()!=target:raise RuntimeError('target changed')
            phase='reserve_once'
            repo=os.environ['GITHUB_REPOSITORY']
            req=Request('https://api.github.com/repos/'+repo+'/git/refs',data=json.dumps({'ref':'refs/heads/douyin-authorized-test/native-heart-20261102','sha':os.environ['GITHUB_SHA']}).encode(),headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','Content-Type':'application/json'},method='POST')
            with urlopen(req,timeout=15) as response:
                if response.status!=201:raise RuntimeError('reservation failed')
            phase='submit_once'
            await editor.press('Enter')
            await page.wait_for_timeout(7000)
            final=await page.locator('[class*="is-me-"]').count()
            remaining=await editor.evaluate('(e)=>e.querySelectorAll("img").length')
            print(json.dumps({'submitted_once':True,'new_outgoing_count':final-before,'remaining_editor_images':remaining}))
            # Local page echo does not establish server delivery.
            raise RuntimeError('delivery unverified; do not retry automatically')
        finally:await browser.close()

if __name__=='__main__':
    try:asyncio.run(main())
    except Exception as exc:
        print(json.dumps({'phase':phase,'error':type(exc).__name__,'status':'not verified'}));sys.exit(1)

