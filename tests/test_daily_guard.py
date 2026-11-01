from types import SimpleNamespace
from unittest.mock import patch
import pytest
from app.config import ConfigError
from app.daily_guard import GitHubDailyHistory, validate_task
from app.models import Message, Target


def task(targets):
    return SimpleNamespace(timezone='Asia/Shanghai', prevent_duplicates=True, targets=targets)


def test_validation_and_single_target(monkeypatch):
    a = Target('A', (Message(type='text', content='hi'),))
    validate_task(task((a,)))
    with pytest.raises(ConfigError): validate_task(task((a, a)))
    with pytest.raises(ConfigError): validate_task(task((Target('A', a.messages * 2),)))
    monkeypatch.setenv('LIVE_SINGLE_TARGET', 'true')
    with pytest.raises(ConfigError): validate_task(task((a, Target('B', a.messages))))


def test_reservation_is_daily_per_target_and_fails_closed(monkeypatch):
    for key, value in {'GITHUB_REPOSITORY':'test/repo', 'GH_GUARD_TOKEN':'dummy', 'GITHUB_SHA':'a'*40}.items(): monkeypatch.setenv(key, value)
    guard = GitHubDailyHistory()
    key = guard.key('one', '2026-11-01', 'friend', 'message-a')
    assert key == guard.key('changed', '2026-11-01', 'friend', 'message-b')
    assert key != guard.key('one', '2026-11-02', 'friend', 'message-a')
    with patch.object(guard, '_request', return_value=201): guard.reserve(key)
    with patch.object(guard, '_request', return_value=422):
        with pytest.raises(RuntimeError): guard.reserve(key)
    with patch.object(guard, '_request', return_value=503):
        with pytest.raises(RuntimeError): guard.contains(key)

@pytest.mark.asyncio
async def test_dry_run_never_calls_sender(monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock
    import app.main as main
    from app.models import Settings, TaskConfig
    target = Target('friend', (Message(type='text', content='hello'),))
    cfg = TaskConfig('daily', 'Asia/Shanghai', (target,), {}, 0, 0, True, True)
    settings = Settings(tmp_path/'config.json', None, '[]', True, None, tmp_path/'artifacts', False)
    @asynccontextmanager
    async def browser(_):
        yield SimpleNamespace(page=MagicMock(), context=MagicMock())
    sender = AsyncMock()
    monkeypatch.setenv('GITHUB_DAILY_GUARD', 'true')
    monkeypatch.setattr(main, 'load_settings', lambda _: settings)
    monkeypatch.setattr(main, 'load_task', lambda _: cfg)
    monkeypatch.setattr(main, 'open_douyin', browser)
    monkeypatch.setattr(main, 'open_private_messages', AsyncMock())
    monkeypatch.setattr(main, '_open_target_with_retry', AsyncMock())
    monkeypatch.setattr(main, 'send_message', sender)
    assert await main.run(dry_run=True) == 0
    sender.assert_not_awaited()

