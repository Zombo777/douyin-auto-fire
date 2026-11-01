from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.main as main
from app.douyin import PageOperationError, SendRejectedError
from app.errors import ErrorCategory, classify_error, get_retry_strategy, should_stop_all_tasks
from app.models import Message, Settings, Target, TaskConfig
from app.sender import _await_send_terminal_state


def test_rejection_has_own_category_without_inventing_a_cause():
    error = SendRejectedError("文字发送失败，页面提示可以重试")
    assert isinstance(error, PageOperationError)
    assert classify_error(error) is ErrorCategory.SEND_REJECTED
    assert classify_error(error) not in {ErrorCategory.RATE_LIMIT, ErrorCategory.AUTHENTICATION}
    assert get_retry_strategy(error).max_retries == 0
    assert not get_retry_strategy(error).should_retry(0)
    assert should_stop_all_tasks(error)


@pytest.mark.asyncio
async def test_explicit_failure_marker_raises_typed_rejection():
    page = MagicMock()
    scope = MagicMock()
    marker = MagicMock()
    marker.count = AsyncMock(return_value=1)
    marker.is_visible = AsyncMock(return_value=True)
    scope.locator.return_value.first = marker
    with pytest.raises(SendRejectedError, match="发送失败"):
        await _await_send_terminal_state(page, scope, "文字")
    page.keyboard.press.assert_not_called()


@pytest.mark.asyncio
async def test_rejection_stops_before_next_target_and_does_not_mark_success(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_DAILY_GUARD", raising=False)
    settings = Settings(tmp_path / "config.json", None, "[]", True, None, tmp_path / "artifacts", False)
    message = Message(type="text", content="test")
    task = TaskConfig("test", "Asia/Shanghai", (Target("first", (message,)), Target("second", (message,))),
                      {}, 0, 0, True, True)
    page = MagicMock()
    @asynccontextmanager
    async def browser(_settings):
        yield SimpleNamespace(page=page, context=MagicMock())
    history = MagicMock()
    history.contains.return_value = False
    history.run_date.return_value = "2026-11-02"
    chat = MagicMock()
    chat.open_target = AsyncMock()
    sender = AsyncMock(side_effect=SendRejectedError("文字发送失败，页面提示可以重试"))
    monkeypatch.setattr(main, "load_settings", lambda _: settings)
    monkeypatch.setattr(main, "load_task", lambda _: task)
    monkeypatch.setattr(main, "open_douyin", browser)
    monkeypatch.setattr(main, "History", lambda _: history)
    monkeypatch.setattr(main, "DouyinChat", lambda *args, **kwargs: chat)
    monkeypatch.setattr(main, "open_private_messages", AsyncMock())
    monkeypatch.setattr(main, "verify_login", AsyncMock())
    monkeypatch.setattr(main, "send_message", sender)
    monkeypatch.setattr(main, "_screenshot", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "_notify_dingtalk", AsyncMock())
    monkeypatch.setattr(main, "_notify_webhook", AsyncMock())
    monkeypatch.setattr(main, "_configure_logging", lambda *args, **kwargs: None)
    with pytest.raises(SendRejectedError):
        await main.run()
    chat.open_target.assert_awaited_once_with("first", retries=0)
    sender.assert_awaited_once()
    history.reserve.assert_called_once()
    history.mark_success.assert_not_called()

