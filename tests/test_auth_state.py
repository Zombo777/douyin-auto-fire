from unittest.mock import AsyncMock, MagicMock
import json
import pytest
from app.models import Settings
from app.browser import open_douyin

@pytest.mark.asyncio
@pytest.mark.parametrize('state_mode', [True, False])
async def test_auth_preserves_full_state_or_normalizes_cookie_array(monkeypatch, tmp_path, state_mode):
    monkeypatch.delenv('NETWORK_DIAGNOSTICS', raising=False)
    monkeypatch.setenv('BROWSER_CHANNEL', 'chrome')
    cookies = [{'name':'test','value':'dummy','domain':'.douyin.com','path':'/'}]
    state = {'cookies':cookies, 'origins':[{'origin':'https://www.douyin.com','localStorage':[{'name':'login','value':'dummy'}]}]}
    auth = state if state_mode else cookies
    settings = Settings(tmp_path/'config.json', None, json.dumps(auth), True, None, tmp_path/'artifacts', False)
    page = MagicMock()
    context = MagicMock(new_page=AsyncMock(return_value=page), add_cookies=AsyncMock(), close=AsyncMock())
    browser = MagicMock(new_context=AsyncMock(return_value=context), close=AsyncMock())
    pw = MagicMock(stop=AsyncMock())
    pw.chromium.launch = AsyncMock(return_value=browser)
    provider = MagicMock(start=AsyncMock(return_value=pw))
    monkeypatch.setattr('app.browser.async_playwright', lambda: provider)
    async with open_douyin(settings): pass
    pw.chromium.launch.assert_awaited_once_with(headless=True, channel='chrome')
    if state_mode:
        assert browser.new_context.await_args.kwargs['storage_state'] == state
        context.add_cookies.assert_not_awaited()
    else:
        assert 'storage_state' not in browser.new_context.await_args.kwargs
        assert context.add_cookies.await_args.args[0][0]['name'] == 'test'

