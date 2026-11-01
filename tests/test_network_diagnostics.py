import asyncio
import json
from types import SimpleNamespace

import pytest

from app.network_diagnostics import NetworkDiagnostics, MAX_JSON_RESPONSES, _category


class Emitter:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.listeners = {}

    def on(self, event, handler):
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self.listeners[event].remove(handler)

    def emit(self, event, value):
        for handler in list(self.listeners.get(event, [])):
            handler(value)


class Response:
    status = 200
    url = "https://imapi.douyin.com/v1/message/send?cookie=COOKIE_SECRET&friend=PRIVATE_NAME"

    async def json(self):
        return {"status_code": 1003, "code": "TOKEN_SECRET", "message": "CHAT_SECRET",
                "status": True, "error_code": 191270055, "data": {"token": "TOKEN_SECRET"}}


@pytest.mark.asyncio
async def test_diagnostics_never_exposes_sensitive_urls_content_or_frames():
    page = Emitter()
    observer = NetworkDiagnostics(page)
    page.emit("response", Response())
    page.emit("requestfailed", SimpleNamespace(url=Response.url,
        failure="net::ERR_CONNECTION_RESET https://secret.example/?token=TOKEN_SECRET"))
    socket = Emitter(url="wss://imapi.douyin.com/ws?token=TOKEN_SECRET")
    page.emit("websocket", socket)
    socket.emit("framesent", "COOKIE_SECRET PRIVATE_NAME CHAT_SECRET")
    socket.emit("framereceived", b"TOKEN_SECRET")
    result = await observer.finish()
    encoded = json.dumps(result)
    for secret in ("COOKIE_SECRET", "PRIVATE_NAME", "CHAT_SECRET", "TOKEN_SECRET", "https://", "/v1/"):
        assert secret not in encoded
    assert result["numeric_status_counts"] == {"im:status_code:1003": 1}
    assert result["request_failure_counts"] == {"im:ERR_CONNECTION_RESET": 1}
    assert result["websocket_counts"] == {"im:opened": 1, "im:received": 1, "im:sent": 1}
    assert all(not callbacks for callbacks in page.listeners.values())
    assert all(not callbacks for callbacks in socket.listeners.values())


@pytest.mark.asyncio
async def test_ignores_json_on_untrusted_hosts_and_bounds_async_work():
    page = Emitter()
    observer = NetworkDiagnostics(page)
    class Untrusted(Response):
        url = "https://douyin.com.attacker.example/im/secret"
        async def json(self):
            raise AssertionError("untrusted body must not be read")
    page.emit("response", Untrusted())
    for _ in range(100):
        page.emit("response", Response())
        await asyncio.sleep(0)
    result = await observer.finish()
    assert result["json_responses_examined"] <= MAX_JSON_RESPONSES
    assert result["json_responses_skipped"] > 0
    assert _category(Untrusted.url) == "other"


@pytest.mark.asyncio
async def test_body_error_and_unknown_network_error_are_not_disclosed():
    page = Emitter()
    observer = NetworkDiagnostics(page)
    class Broken(Response):
        async def json(self):
            raise ValueError("COOKIE_SECRET CHAT_SECRET")
    page.emit("response", Broken())
    page.emit("requestfailed", SimpleNamespace(url=Response.url, failure="COOKIE_SECRET ERR_PRIVATE_NAME"))
    result = await observer.finish()
    assert result["numeric_status_counts"] == {}
    assert result["request_failure_counts"] == {"im:OTHER": 1}
    assert "COOKIE_SECRET" not in json.dumps(result)

