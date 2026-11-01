# 本 Fork 的 GitHub Actions 部署

## 当前状态

- 发送工作流：`.github/workflows/send.yml`。
- 唯一定时：`30 14 * * *`、`timezone: Asia/Shanghai`，北京时间每天 14:30。
- 手动执行默认 `dry_run=true`，不会输入或发送任何消息。
- 只有仓库变量 `PRODUCTION_ENABLED=true` 才允许定时真实发送。真实测试通过并得到用户确认之前保持关闭。
- `LIVE_TEST_ENABLED` 当前关闭。临时的一次性测试入口受永久原子占位保护，不能靠重跑再次发送。
- GitHub-hosted Runner 曾出现真实发送失败和 Cookie 失效。自动化尚未完成实际送达验收；不能把测试全绿或 Dry Run 通过当成真实送达。

## 两个 Secrets

在 Settings → Secrets and variables → Actions → Secrets 配置：

1. `DOUYIN_COOKIE`：本人在已登录的抖音网页导出的完整 Cookie JSON 数组。
2. `DOUYIN_CONFIG`：以 `config.daily.example.json` 为模板的 JSON。好友名单、文字和 guard_key 都保存在这个 Secret 内。

不要将凭证发到聊天、提交到 Git、上传到 Issue 或第三方配置生成器。Cookie 失效后必须本人重新登录并更新 Secret，程序不会绕过验证码或安全验证。

`guard_key` 至少 32 字符，可在 Windows PowerShell 自行生成并复制：

```powershell
[guid]::NewGuid().ToString('N') | Set-Clipboard
```

将剪贴板内容粘贴到配置的 guard_key 字符串内。以后更新 Cookie、好友或消息时保留同一个 guard_key；更换该值会改变去重标识。

## 消息与去重

- 一个抖音账号可配置多个好友，每人一条文字或一条随机文字模板。随机模板的 choices 仅允许 text。
- `send_interval_seconds` 控制好友之间的间隔，例为 5–10 秒。
- 必须 `prevent_duplicates=true` 且 `timezone=Asia/Shanghai`。
- 以好友的精确配置名和北京时间日期生成 HMAC 标识。发送前在本仓库原子创建 Git ref，重试、并发或新 Runner 都会保留这条记录。
- 去重与消息内容、task_id 无关；同日改消息不会再发。不要为同一好友配置不同名称；改昵称前需要检查当天是否已经发送。
- 占位先于发送，因此断电、超时或结果不确定时可能少发，绝不自动补发。GitHub 或网络无法确认占位时停止。
- 仓库令牌仅需 `contents: write`，用于持久占位；其余权限关闭。checkout 不保留令牌。
- 每个好友每天会增加一个轻量分支 ref；其中没有 Cookie、消息或明文好友名。勿删除当天记录。

## 安全与运行

使用 Playwright 官方 `channel=chrome`（Google Chrome 稳定版），不添加 stealth、指纹伪装、代理或绕过验证码的代码。
网络诊断仅输出分类、状态码、连接错误和 WebSocket 帧数；不输出 URL、请求头、Cookie、消息正文或帧内容。
工作流关闭 Trace，也不上传日志目录、页面截图或浏览器状态。可在 Actions 的脱敏日志中查看结果。
页面明确显示发送失败时停止后续发送，不点击重试。认证失效或安全验证也立即停止。

## 验收顺序

1. Test 工作流：模拟测试、浏览器 DOM 测试，无真实账号发送。
2. Send 工作流勾选 dry_run：验证登录与好友定位，无真实发送。
3. 用户明确确认后，单好友一条消息真实测试；必须查看运行结果并由本人核对聊天。
4. 实际送达通过并再次得到用户确认后，才设置 `PRODUCTION_ENABLED=true`。

如出现发送失败，保留原记录、关闭真实发送，先调查原因。Dry Run 不代表具备真实发送权限。上游同类问题：
https://github.com/unmev/douyin-auto-fire/issues/30

## 长期限制

GitHub 的 cron 不是准点送达保证：高负载可能延迟，极端情况可能丢弃任务。公开仓库连续 60 天无活动会禁用定时；需要定期查看 Actions 和失败通知，不依赖一次性部署永久无人维护。
仅默认分支上的 schedule 生效。不要同时配置外部 cron、Windows 定时、Docker cron 或另一个仓库向同一账号发送。
没有创建额外的自动补发定时。Cookie 或平台规则变化仍可能需要本人处理。

GitHub 官方 schedule 说明：
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule


