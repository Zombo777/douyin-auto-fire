"""Bounded, content-free network diagnostics for an existing browser page.

Never exposes URLs, request/response bodies, credentials or WebSocket frames.
This observer does not issue requests or alter browser traffic.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from urllib.parse import urlsplit


MAX_JSON_RESPONSES = 16
MAX_PENDING_TASKS = 4
MAX_COUNTER = 9999
STATUS_FIELDS = ("status_code", "error_code", "code", "status")
NET_CODES = frozenset({
    "ERR_ABORTED", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_TIMED_OUT", "ERR_TIMED_OUT",
    "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED", "ERR_FAILED",
    "ERR_HTTP_RESPONSE_CODE_FAILURE", "ERR_NETWORK_CHANGED", "ERR_SSL_PROTOCOL_ERROR",
    "ERR_CERT_AUTHORITY_INVALID", "ERR_CERT_DATE_INVALID",
})


def _category(url: object) -> str:
    if not isinstance(url, str):
        return "other"
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not any(
            host == suffix or host.endswith("." + suffix)
            for suffix in ("douyin.com", "amemv.com", "snssdk.com")
        ):
            return "other"
        path = parsed.path.lower()
        if "passport" in host or re.search(r"/(?:passport|auth|login)(?:/|$)", path):
            return "auth"
        if host.startswith(("im.", "imapi.", "imapi-")) or re.search(
            r"/(?:im|message|messages|conversation|conversations)(?:/|$)", path
        ):
            return "im"
    except (ValueError, TypeError):
        pass
    return "other"


class NetworkDiagnostics:
    """Observe only aggregate transport events; call ``finish`` before teardown."""

    def __init__(self, page) -> None:
        self.page = page
        self.active = True
        self.http = Counter()
        self.json_codes = Counter()
        self.failures = Counter()
        self.websockets = Counter()
        self.tasks: set[asyncio.Task] = set()
        self.json_count = 0
        self.skipped_json = 0
        self.socket_listeners = []
        self.listeners = [("response", self._response), ("requestfailed", self._failed),
                          ("websocket", self._websocket)]
        for event, handler in self.listeners:
            page.on(event, handler)

    @staticmethod
    def _increment(counter: Counter, key: str) -> None:
        counter[key] = min(MAX_COUNTER, counter[key] + 1)

    def _response(self, response) -> None:
        if not self.active:
            return
        category = _category(response.url)
        status = response.status
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            self._increment(self.http, f"{category}:{status}")
        if category not in {"auth", "im"}:
            return
        if self.json_count >= MAX_JSON_RESPONSES or len(self.tasks) >= MAX_PENDING_TASKS:
            self.skipped_json = min(MAX_COUNTER, self.skipped_json + 1)
            return
        self.json_count += 1
        task = asyncio.create_task(self._numeric_status(response, category))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _numeric_status(self, response, category: str) -> None:
        try:
            # A short timeout bounds unfinished bodies. Only top-level numeric
            # status fields are retained; binary and non-JSON responses are ignored.
            payload = await asyncio.wait_for(response.json(), timeout=1.0)
            if not isinstance(payload, dict):
                return
            for field in STATUS_FIELDS:
                value = payload.get(field)
                if type(value) is int and -1_000_000 <= value <= 1_000_000:
                    self._increment(self.json_codes, f"{category}:{field}:{value}")
        except (Exception, asyncio.CancelledError):
            # Exceptions can contain response text/URLs. Never retain or print them.
            return

    def _failed(self, request) -> None:
        if not self.active:
            return
        failure = request.failure
        matched = re.search(r"\b(?:net::)?(ERR_[A-Z_]+)\b", failure or "")
        code = matched.group(1) if matched and matched.group(1) in NET_CODES else "OTHER"
        self._increment(self.failures, f"{_category(request.url)}:{code}")

    def _websocket(self, socket) -> None:
        if not self.active:
            return
        category = _category(socket.url.replace("wss://", "https://", 1).replace("ws://", "http://", 1))
        self._increment(self.websockets, f"{category}:opened")
        # Limit listener retention even if a page opens many sockets.
        if len(self.socket_listeners) >= 16:
            return
        handlers = []
        for event, label in (("framesent", "sent"), ("framereceived", "received")):
            def handler(_frame, label=label):
                if self.active:
                    self._increment(self.websockets, f"{category}:{label}")
            socket.on(event, handler)
            handlers.append((event, handler))
        self.socket_listeners.append((socket, handlers))

    async def finish(self) -> dict:
        self.active = False
        for event, handler in self.listeners:
            self.page.remove_listener(event, handler)
        for socket, handlers in self.socket_listeners:
            for event, handler in handlers:
                socket.remove_listener(event, handler)
        pending = list(self.tasks)
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=1.5)
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        return {
            "http_status_counts": dict(sorted(self.http.items())),
            "numeric_status_counts": dict(sorted(self.json_codes.items())),
            "request_failure_counts": dict(sorted(self.failures.items())),
            "websocket_counts": dict(sorted(self.websockets.items())),
            "json_responses_examined": self.json_count,
            "json_responses_skipped": self.skipped_json,
        }

