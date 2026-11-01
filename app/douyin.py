from __future__ import annotations

import asyncio
import json
import logging
import re

from playwright.async_api import Locator, Page

from app.selectors import CHAT_PANEL_MARKERS, MESSAGE_INPUTS, SEARCH_INPUTS

LOGGER = logging.getLogger("douyin_sender")

# Fixed structural labels only. Never read/render element text or arbitrary
# attributes: a chat page contains credentials, names and private messages.
_SHAPE_FAMILIES = ("RightPanelHeader", "chatHeader", "ChatHeader", "messageContent",
                   "chatContent", "MessagePanel", "messageEditor", "messageMsgInput",
                   "DraftEditor", "SearchPanel", "conversationConversationItem")
_SHAPE_GROUPS = {
    "headers": '[class*="RightPanelHeader"], [class*="chatHeader"], [class*="ChatHeader"]',
    "panels": '[class*="messageContent"], [class*="chatContent"], [class*="MessagePanel"]',
    "composers": '[contenteditable], textarea, [role="textbox"], [class*="messageMsgInput"], [class*="messageEditor"], [class*="DraftEditor"]',
    "search": '[class*="SearchPanel"], input[placeholder*="搜索"]',
}
_CHAT_SHAPE_JS = """({groups, families}) => {
    const visible = el => {
        const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const result = {};
    for (const [group, selector] of Object.entries(groups)) {
        const nodes = [...document.querySelectorAll(selector)];
        result[group] = {
            count: nodes.length,
            visible_count: nodes.filter(visible).length,
            shapes: nodes.slice(0, 12).map(el => ({
                tag: el.tagName.toLowerCase(), role: el.getAttribute('role'),
                editable: el.isContentEditable, visible: visible(el),
                class_families: families.filter(f => [...el.classList].some(c => c.includes(f)))
            }))
        };
    }
    return result;
}"""


async def _collect_chat_structure(page: Page) -> dict:
    try:
        raw = await asyncio.wait_for(page.evaluate(_CHAT_SHAPE_JS, {
            "groups": _SHAPE_GROUPS, "families": _SHAPE_FAMILIES,
        }), timeout=3.0)
    except Exception:
        return {"diagnostic_unavailable": True}
    if not isinstance(raw, dict):
        return {"diagnostic_unavailable": True}
    result = {}
    tags = {"div", "span", "input", "textarea", "button", "section", "main", "form", "svg"}
    roles = {"textbox", "button", "dialog", "searchbox", "combobox", "listbox", "option"}
    for group in _SHAPE_GROUPS:
        value = raw.get(group)
        if not isinstance(value, dict):
            continue
        safe = {key: min(10000, max(0, value[key])) for key in ("count", "visible_count")
                if type(value.get(key)) is int}
        safe["shapes"] = []
        shapes = value.get("shapes", [])
        for shape in (shapes[:12] if isinstance(shapes, list) else []):
            if not isinstance(shape, dict):
                continue
            families = shape.get("class_families", [])
            tag, role = shape.get("tag"), shape.get("role")
            safe["shapes"].append({
                "tag": tag if isinstance(tag, str) and tag in tags else "other",
                "role": role if isinstance(role, str) and role in roles else None,
                "editable": shape.get("editable") is True,
                "visible": shape.get("visible") is True,
                "class_families": [f for f in _SHAPE_FAMILIES if isinstance(families, list) and f in families],
            })
        result[group] = safe
    # Counts only: detect a conversation rendered in a popup or child frame
    # without exposing any URL, title, account or message content.
    pages = page.context.pages
    frames = page.frames
    if isinstance(pages, list):
        result["page_count"] = min(100, len(pages))
    if isinstance(frames, list):
        result["frame_count"] = min(100, len(frames))
    return result


class PageOperationError(RuntimeError):
    pass


class SendRejectedError(PageOperationError):
    """The page explicitly rejected a send; the underlying cause is unknown."""


RETRY_DELAY_MS = 3_000


class DouyinChat:
    def __init__(
        self,
        page: Page,
        timeout_ms: int = 15_000,
        confirm_timeout_ms: int = 15_000,
    ) -> None:
        self.page = page
        self.timeout_ms = timeout_ms
        self.confirm_timeout_ms = confirm_timeout_ms

    async def open_target(self, name: str, retries: int = 1) -> None:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                await self._open_target_once(name)
                return
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    await self.page.wait_for_timeout(RETRY_DELAY_MS)
        if last_error is not None:
            raise last_error
        raise PageOperationError("打开聊天失败")

    async def _open_target_once(self, name: str) -> None:
        # Reuse an existing visible conversation before asking SearchPanel to
        # create/open a session. The search action can leave the page unchanged
        # even though its lookup API succeeded. No messages are sent here.
        existing = await self._existing_conversation(name)
        if existing is not None:
            await existing.click()
            try:
                await self._confirm_opened(name, timeout_ms=min(5_000, self.confirm_timeout_ms))
                LOGGER.info("已从已有会话列表确认目标聊天与输入框")
                return
            except PageOperationError:
                LOGGER.info("已有会话尚未就绪，改用精确好友搜索")
        search = await first_visible(self.page, SEARCH_INPUTS, self.timeout_ms)
        await search.click()
        await search.fill("")
        await search.fill(name)
        await self.page.wait_for_timeout(1_500)

        result = await self._search_result(name)
        if result is None:
            raise PageOperationError("搜索不到目标好友")
        await result.click()
        await self._confirm_opened(name)

    async def _existing_conversation(self, name: str) -> Locator | None:
        title_selectors = (
            '[class*="conversationConversationItemtitle"]',
            '[class*="ConversationItemtitle"]', '[class*="ConversationItemTitle"]',
            '[class*="conversation-item-title"]', '[class*="conversation-item-Title"]',
        )
        for selector in ('[data-e2e="conversation-item"]', '[class*="conversationConversationItem"]',
                         '[class*="conversation-item"]', '[class*="ConversationItem"]'):
            try:
                rows = self.page.locator(selector)
                for index in range(await rows.count()):
                    row = rows.nth(index)
                    if (await row.is_visible() and
                            await _visible_exact_text_locator(row, title_selectors, name) is not None):
                        return row
            except Exception:
                continue
        return None

    async def _search_result(self, name: str) -> Locator | None:
        # Search mode renders a separate SearchPanel. Its "发消息" action is the
        # correct control; clicking the hidden conversation cache does not mount
        # the composer.
        #
        # Identity must be resolved from the per-result name node, never from the
        # collection container: `[class*="SearchPanelitem"]` also matches an outer
        # `SearchPanelitems` wrapper, whose descendants would then contain the
        # target name while `.first` returns another row's button. Scope to result
        # rows and require the matched name node and its button to be visible here.
        search_items = self.page.locator('[class*="SearchPanelitembox"], [class*="SearchPanelitem-box"], [class*="SearchPanelitem_box"]')
        name_selectors = (
            '[class*="SearchPanelitemtitle"]',
            '[class*="SearchPanelitemTitle"]',
            '[class*="SearchPanelitem_title"]',
            '[class*="SearchPanelitem-title"]',
            '[class*="SearchPanelitemname"]',
            '[class*="SearchPanelitemName"]',
            '[class*="SearchPanelitem_name"]',
            '[class*="SearchPanelitem-name"]',
        )

        # Two-phase priority: an exact friend name always wins over a group whose
        # display name happens to start with it. Pass 1 scans every search row for
        # an exact name; only if none is found does pass 2 accept a group member
        # count suffix like "4161(7)" for target "4161". This ordering guarantees
        # "test" never returns "test(7)" or "test1".
        for index in range(await search_items.count()):
            item = search_items.nth(index)
            name_locator = await _visible_exact_text_locator(item, name_selectors, name)
            if name_locator is None:
                continue
            button = item.locator('[class*="SearchPanelitemchat_btn"]').first
            try:
                if await button.count() and await button.is_visible():
                    return button
            except Exception:
                continue

        for index in range(await search_items.count()):
            item = search_items.nth(index)
            name_locator = await _visible_group_text_locator(item, name_selectors, name)
            if name_locator is None:
                continue
            button = item.locator('[class*="SearchPanelitemchat_btn"]').first
            try:
                if await button.count() and await button.is_visible():
                    return button
            except Exception:
                continue

        # The nickname node can be hidden while its conversation row is visible.
        # Locate and click the complete row instead of relying on text visibility.
        row_selectors = (
            '[data-e2e="conversation-item"]',
            '[class*="conversationConversationItem"]',
            '[class*="conversation-item"]',
            '[class*="ConversationItem"]',
        )
        title_selectors = (
            '[class*="conversationConversationItemtitle"]',
            '[class*="ConversationItemtitle"]',
            '[class*="ConversationItemTitle"]',
            '[class*="conversation-item-title"]',
            '[class*="conversation-item-Title"]',
        )
        for selector in row_selectors:
            rows = self.page.locator(selector)
            for index in range(await rows.count()):
                row = rows.nth(index)
                title_locator = await _visible_exact_text_locator(row, title_selectors, name)
                if title_locator is None:
                    continue
                try:
                    if await row.is_visible():
                        return row
                except Exception:
                    continue

        # Second-phase group suffix over conversation rows (same priority rule).
        for selector in row_selectors:
            rows = self.page.locator(selector)
            for index in range(await rows.count()):
                row = rows.nth(index)
                title_locator = await _visible_group_text_locator(row, title_selectors, name)
                if title_locator is None:
                    continue
                try:
                    if await row.is_visible():
                        return row
                except Exception:
                    continue

        # Some Douyin builds render the title itself as hidden, but keep a visible
        # ancestor as the actionable result. Find that ancestor from the hidden title.
        # This hidden-title fallback stays STRICT exact only: a hidden stale name
        # node (group or plain) must never be trusted to resolve the recipient.
        hidden_titles = self.page.locator('[class*="conversationConversationItemtitle"]')
        for index in range(await hidden_titles.count()):
            title = hidden_titles.nth(index)
            if not await _text_equals(title, name):
                continue
            row = title.locator(
                "xpath=ancestor::*[contains(@class, 'conversationConversationItem')][1]"
            )
            if await row.count() and await row.is_visible():
                return row

        return None

    async def message_input(self) -> Locator:
        return await first_visible(self.page, MESSAGE_INPUTS, self.timeout_ms)

    async def _confirm_opened(self, name: str, timeout_ms: int | None = None) -> None:
        timeout = timeout_ms if timeout_ms is not None else self.confirm_timeout_ms
        deadline = asyncio.get_running_loop().time() + timeout / 1000
        while True:
            last_error = await self._chat_open_error(name)
            if last_error is None:
                return
            if asyncio.get_running_loop().time() >= deadline:
                LOGGER.error("聊天打开结构诊断: %s", json.dumps(await _collect_chat_structure(self.page), ensure_ascii=False))
                raise last_error
            await self.page.wait_for_timeout(500)

    async def _chat_open_error(self, name: str) -> PageOperationError | None:
        # Confirm the right-side current chat by the authoritative chat title, which
        # must itself be visible. A visible header retaining a hidden stale name node
        # (common during SPA transitions) must not confirm the wrong recipient, and
        # a secondary username/title field must never substitute for the chat title.
        title_selectors = (
            '[class*="RightPanelHeadertitle"]',
            '[class*="RightPanelHeaderTitle"]',
            '[class*="RightPanelHeader_title"]',
            '[class*="RightPanelHeader-title"]',
            '[class*="chatHeadertitle"]',
            '[class*="ChatHeaderTitle"]',
            '[class*="chatHeader_title"]',
            '[class*="ChatHeader-title"]',
            '[class*="name"]',
            '[class*="Name"]',
            '[class*="nickname"]',
            '[class*="Nickname"]',
        )
        title_matches = False
        for selector in CHAT_PANEL_MARKERS[:3]:
            headers = self.page.locator(selector)
            for index in range(await headers.count()):
                header = headers.nth(index)
                try:
                    if not await header.is_visible():
                        continue
                except Exception:
                    continue
                if await _visible_exact_or_group_text_in(header, title_selectors, name):
                    title_matches = True
                    break
            if title_matches:
                break

        composer_visible = await self._composer_visible()
        if title_matches and composer_visible:
            return None
        return PageOperationError(
            f"点击搜索结果后无法确认聊天已打开（输入框: {'有' if composer_visible else '无'}）"
        )

    async def _composer_visible(self) -> bool:
        for selector in MESSAGE_INPUTS:
            locators = self.page.locator(selector)
            try:
                for index in range(await locators.count()):
                    if await locators.nth(index).is_visible():
                        return True
            except Exception:
                continue
        return False


async def _visible_exact_text_in(container: Locator, selectors: tuple[str, ...], expected: str) -> bool:
    return await _visible_exact_text_locator(container, selectors, expected) is not None


async def _visible_exact_text_locator(
    container: Locator, selectors: tuple[str, ...], expected: str
) -> Locator | None:
    for selector in selectors:
        nodes = container.locator(selector)
        for index in range(await nodes.count()):
            node = nodes.nth(index)
            if await _text_equals(node, expected):
                try:
                    if await node.is_visible():
                        return node
                except Exception:
                    continue
    return None


async def _visible_group_text_locator(
    container: Locator, selectors: tuple[str, ...], expected: str
) -> Locator | None:
    # Second-phase match for group chats: accepts a trailing "(N)"/"（N）"
    # member count. Only reached after the exact pass found nothing, so a bare
    # "test" never reaches here when an exact "test" row exists.
    for selector in selectors:
        nodes = container.locator(selector)
        for index in range(await nodes.count()):
            node = nodes.nth(index)
            if await _group_name_matches(node, expected):
                try:
                    if await node.is_visible():
                        return node
                except Exception:
                    continue
    return None


async def _visible_exact_or_group_text_in(
    container: Locator, selectors: tuple[str, ...], expected: str
) -> bool:
    # Chat-header confirmation: an exact title wins; otherwise a group member
    # count suffix also confirms. The node must be visible in both cases, so a
    # hidden stale title node can never confirm the wrong recipient.
    if await _visible_exact_text_locator(container, selectors, expected) is not None:
        return True
    return await _visible_group_text_locator(container, selectors, expected) is not None


async def _has_exact_text_in(container: Locator, selectors: tuple[str, ...], expected: str) -> bool:
    for selector in selectors:
        if await _has_exact_text(container.locator(selector), expected):
            return True
    return False


async def _has_exact_text(locators: Locator, expected: str) -> bool:
    for index in range(await locators.count()):
        if await _text_equals(locators.nth(index), expected):
            return True
    return False


async def _text_equals(locator: Locator, expected: str) -> bool:
    try:
        return (await locator.inner_text(timeout=500)).strip() == expected
    except Exception:
        return False


# Matches a group chat display name: the configured target name optionally
# followed by exactly one pair of brackets containing a pure member count,
# e.g. "4161" / "4161(7)" / "4161（123）". This is an INDEPENDENT helper kept
# separate from _text_equals so the friend exact-match semantics stay strict:
# it must never let "test" match "test1" (no trailing brackets to legitimize a
# longer name). re.fullmatch anchors both ends; re.escape makes the name literal.
_GROUP_COUNT_SUFFIX_RE_TEMPLATE = r"{name}\s*[\(（]\s*\d+\s*[\)）]"


def _group_count_suffix_matches(actual: str, expected: str) -> bool:
    actual = actual.strip()
    expected = expected.strip()
    if actual == expected:
        return True
    pattern = _GROUP_COUNT_SUFFIX_RE_TEMPLATE.format(name=re.escape(expected))
    return re.fullmatch(pattern, actual) is not None


async def _group_name_matches(locator: Locator, expected: str) -> bool:
    try:
        return _group_count_suffix_matches(
            await locator.inner_text(timeout=500), expected
        )
    except Exception:
        return False


async def first_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 15_000) -> Locator:
    # A SPA may keep an old hidden composer before the visible current one.
    # Inspect every match rather than letting .first hide the actionable node.
    # One overall deadline includes locator RPCs and visibility waits.
    deadline = asyncio.get_running_loop().time() + max(0, timeout_ms) / 1000

    async def wait_for_visible() -> Locator:
        while True:
            for selector in selectors:
                locators = page.locator(selector)
                try:
                    count = await locators.count()
                except Exception:
                    continue
                for index in range(count):
                    locator = locators.nth(index)
                    try:
                        if not await locator.is_visible():
                            continue
                        remaining = int((deadline - asyncio.get_running_loop().time()) * 1000)
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        await locator.wait_for(state="visible", timeout=min(200, remaining))
                        return locator
                    except Exception:
                        continue
            await page.wait_for_timeout(100)

    try:
        return await asyncio.wait_for(wait_for_visible(), timeout=max(0, timeout_ms) / 1000)
    except asyncio.TimeoutError as exc:
        raise PageOperationError(f"找不到页面元素，已尝试: {', '.join(selectors)}") from exc

