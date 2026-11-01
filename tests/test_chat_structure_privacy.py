import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.douyin import DouyinChat, PageOperationError, _collect_chat_structure, _CHAT_SHAPE_JS
from app.network_diagnostics import _category


@pytest.mark.asyncio
async def test_chat_structure_keeps_only_fixed_labels_and_numeric_shape():
    secret = "PRIVATE_NAME_COOKIE_CHAT"
    page = MagicMock()
    page.evaluate = AsyncMock(return_value={
        "headers": {"count": 1, "visible_count": 1, "innerText": secret,
                    "shapes": [{"tag": "div", "role": secret, "value": secret, "title": secret,
                                "editable": False, "visible": True,
                                "class_families": ["RightPanelHeader", secret]}]},
        "composers": {"count": 0, "visible_count": 0, "shapes": []},
        secret: {"count": 10},
    })
    result = await _collect_chat_structure(page)
    assert secret not in json.dumps(result)
    assert result["headers"]["shapes"] == [{"tag": "div", "role": None, "editable": False,
                                             "visible": True, "class_families": ["RightPanelHeader"]}]
    for forbidden in ("innerText", "innerHTML", "outerHTML", ".value", "getAttribute('title')"):
        assert forbidden not in _CHAT_SHAPE_JS


@pytest.mark.asyncio
async def test_timeout_logs_structure_without_target_or_exception_content(caplog):
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("COOKIE_SECRET"))
    chat = DouyinChat(page, confirm_timeout_ms=0)
    chat._chat_open_error = AsyncMock(return_value=PageOperationError("无法确认聊天已打开"))
    with pytest.raises(PageOperationError):
        await chat._confirm_opened("PRIVATE_FRIEND")
    assert "diagnostic_unavailable" in caplog.text
    assert "PRIVATE_FRIEND" not in caplog.text
    assert "COOKIE_SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_search_result_uses_normal_actionability_click(monkeypatch):
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    search = MagicMock()
    search.click = AsyncMock()
    search.fill = AsyncMock()
    monkeypatch.setattr("app.douyin.first_visible", AsyncMock(return_value=search))
    result = MagicMock()
    result.click = AsyncMock()
    chat = DouyinChat(page)
    chat._search_result = AsyncMock(return_value=result)
    chat._confirm_opened = AsyncMock()
    await chat._open_target_once("friend")
    result.click.assert_awaited_once_with()


def test_frontier_im_has_im_category_only_for_exact_trusted_host():
    assert _category("https://frontier-im.douyin.com/ws?token=secret") == "im"
    assert _category("https://frontier-im.douyin.com.attacker.example/ws") == "other"

