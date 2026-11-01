from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.config import ConfigError

# One additional test explicitly authorized in this session. This ID is an
# idempotency identifier, not a credential; the durable ref consumes it once.
APPROVED_TEST_ID = '20261101-full-chromium-test-1'


def validate_task(task):
    if task.timezone != 'Asia/Shanghai' or not task.prevent_duplicates:
        raise ConfigError('必须使用 Asia/Shanghai 并开启 prevent_duplicates')
    names = [t.name for t in task.targets]
    if len(set(names)) != len(names):
        raise ConfigError('好友名称不能重复')
    if any(len(t.messages) != 1 or t.messages[0].type not in ('text', 'random') or
           (t.messages[0].type == 'random' and any(c.type != 'text' for c in t.messages[0].choices))
           for t in task.targets):
        raise ConfigError('每位好友只能配置一条文字或随机文字消息')
    if os.getenv('LIVE_SINGLE_TARGET') == 'true' and len(names) != 1:
        raise ConfigError('真实测试只允许一个好友')
    if os.getenv('APPROVED_TEST_ID'):
        if (os.getenv('APPROVED_TEST_ID') != APPROVED_TEST_ID or
                os.getenv('GITHUB_EVENT_NAME') != 'workflow_dispatch' or
                os.getenv('LIVE_SINGLE_TARGET') != 'true' or len(names) != 1 or
                task.targets[0].messages[0].type != 'text' or
                task.targets[0].messages[0].content != '😊'):
            raise ConfigError('本次授权仅允许手动向一个配置好友发送一条微笑表情')


class GitHubDailyHistory:
    """Atomic pre-send reservation via GitHub refs. Unknown results fail closed."""
    def __init__(self):
        self.repo = os.environ['GITHUB_REPOSITORY']
        self.token = os.environ['GH_GUARD_TOKEN']
        self.sha = os.environ['GITHUB_SHA']
        config = json.loads(os.environ['DOUYIN_CONFIG'])
        self.guard_key = config.get('guard_key', '')
        if not isinstance(self.guard_key, str) or len(self.guard_key) < 32:
            raise ConfigError('DOUYIN_CONFIG 的 guard_key 必须是至少 32 位的随机字符串，并长期保持不变')
        self.base = 'https://api.github.com/repos/' + self.repo + '/git/'
        self.daily_keys = {}

    def run_date(self, timezone):
        return datetime.now(ZoneInfo(timezone)).date().isoformat()

    def key(self, task_id, run_date, target, message_id):
        # Stable across task and message edits; only one reservation per name per day.
        digest = hmac.new(self.guard_key.encode('utf-8'), target.encode('utf-8'), hashlib.sha256).hexdigest()
        daily_key = f'douyin-daily/{run_date}/{digest}'
        if os.getenv('APPROVED_TEST_ID') == APPROVED_TEST_ID:
            if os.getenv('LIVE_SINGLE_TARGET') != 'true' or os.getenv('GITHUB_EVENT_NAME') != 'workflow_dispatch':
                raise ConfigError('一次性测试不能用于生产定时运行')
            key = f'douyin-authorized-test/{APPROVED_TEST_ID}/{digest}'
            self.daily_keys[key] = daily_key
            return key
        return daily_key

    def _request(self, method, endpoint, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = Request(self.base + endpoint, data=body, method=method, headers={
            'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=15) as response:
                return response.status
        except HTTPError as exc:
            return exc.code
        except (URLError, TimeoutError) as exc:
            raise RuntimeError('无法确认 GitHub 去重状态，已停止发送') from exc

    def contains(self, key):
        status = self._request('GET', 'ref/heads/' + key)
        if status == 200: return True
        if status == 404: return False
        raise RuntimeError(f'读取去重状态失败 (HTTP {status})')

    def reserve(self, key):
        status = self._request('POST', 'refs', {'ref': 'refs/heads/' + key, 'sha': self.sha})
        if status != 201:
            raise RuntimeError(f'发送前占位失败 (HTTP {status})，未发送')
        # Keep the normal daily reservation, including the previous uncertain
        # attempt. Never delete or overwrite it to authorize this one extra test.
        daily_key = self.daily_keys.get(key)
        if daily_key and not self.contains(daily_key):
            self.reserve(daily_key)

    def mark_success(self, key):
        pass  # reservation is intentionally retained even if delivery is uncertain

