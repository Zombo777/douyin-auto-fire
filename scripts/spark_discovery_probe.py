"""Compare visible conversation IDs in memory; never print identifiers or messages."""
import asyncio, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import parse_auth_json
from playwright.async_api import async_playwright

async def main():
    state=parse_auth_json(os.environ.get('DOUYIN_COOKIE', ''), 'DOUYIN_COOKIE')
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True)
        context=await browser.new_context(storage_state=state,locale='zh-CN')
        try:
            web=await context.new_page()
            await web.goto('https://www.douyin.com/chat',wait_until='domcontentloaded',timeout=60000)
            await web.locator('.conversationConversationItemwrapper').first.wait_for(state='attached',timeout=45000)
            web_ids=set(); web_complete=False; stalls=0
            for _ in range(160):
                data=await web.evaluate('''() => {let rows=[...document.querySelectorAll('.conversationConversationItemwrapper')];let ids=rows.filter(e=>e.querySelector('.commonStreakstreakContainer')).map(row=>{
                  let k=Object.keys(row).find(k=>k.startsWith('__reactFiber')),f=k&&row[k];for(let i=0;f&&i<8;i++,f=f.return){let c=f.memoizedProps?.conversation;if(c?.id!=null)return String(c.id)}return null;
                }).filter(Boolean); let box=rows[0]?.parentElement; while(box && box.scrollHeight<=box.clientHeight+10)box=box.parentElement;
                if(!box)return {ids, moved:false}; let old=box.scrollTop; box.scrollTop+=Math.max(100,box.clientHeight*0.6); return {ids,moved:box.scrollTop>old};}''')
                web_ids.update(data['ids']); stalls=0 if data['moved'] else stalls+1
                if stalls>=40: web_complete=True; break
                await web.wait_for_timeout(300)
            creator=await context.new_page()
            await creator.goto('https://creator.douyin.com/creator-micro/data/following/chat',wait_until='domcontentloaded',timeout=60000)
            await creator.locator('[class*="item-header-name-"]').first.wait_for(state='attached',timeout=45000)
            friend_tab=creator.get_by_text('朋友私信',exact=True)
            if await friend_tab.count()==1 and await friend_tab.is_visible():
                await friend_tab.click()
                await creator.wait_for_timeout(2000)
            creator_ids=set(); creator_complete=False; stalls=0
            for _ in range(240):
                data=await creator.evaluate('''() => {let rows=[...document.querySelectorAll('li')].filter(e=>e.querySelector('[class*="item-header-name-"]'));let ids=rows.map(row=>{
                  let k=Object.keys(row).find(k=>k.startsWith('__reactFiber')),f=k&&row[k];for(let i=0;f&&i<8;i++,f=f.return){let c=f.memoizedProps?.header?.props?.conversation;if(c?.id!=null)return String(c.id)}return null;
                }).filter(Boolean);let box=[...document.querySelectorAll('#sub-app [role="grid"], #sub-app .ReactVirtualized__Grid')].find(x=>x.clientHeight>0&&x.scrollHeight>x.clientHeight);if(!box)return {ids,moved:false};let old=box.scrollTop;box.scrollTop+=Math.max(100,box.clientHeight*0.6);return {ids,moved:box.scrollTop>old};}''')
                creator_ids.update(data['ids']);stalls=0 if data['moved'] else stalls+1
                if stalls>=40: creator_complete=True;break
                await creator.wait_for_timeout(300)
            print(json.dumps({'streak_count':len(web_ids),'creator_count':len(creator_ids),'matched_count':len(web_ids&creator_ids),'web_reached_loaded_end':web_complete,'creator_reached_loaded_end':creator_complete}))
        finally:
            await browser.close()

try:
    asyncio.run(main())
except Exception:
    print('Spark discovery probe failed; no messages were sent.')
    sys.exit(1)

