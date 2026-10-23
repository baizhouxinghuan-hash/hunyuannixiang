# 排障（troubleshooting.md）

## 快速定位

```bash
python tools/selfcheck.py            # 服务自检（起服务 + 真实 HTTP）
python tools/healthcheck.py --direct # 上游可达性 + 登录态 + 协议指纹
python -m pytest -q                  # 离线测试（不需要你的账号）
```

## 常见错误对照表

| 现象 / 报错 | 原因 | 处理 |
| --- | --- | --- |
| HTTP **501** `protocol_unknown` | 该能力仍未抓包确认。当前只有 4 类：文件上传、图片输入、上游原生 `tool_calls`（且 `tools.mode=off`）、消息详情（**设计行为**） | 按响应里的 `hint` 抓包 → 回填 `protocol/current/profile.yaml`（见 `docs/protocol.md`） |
| HTTP **401** `authentication_error` | 未登录 / 会话过期（Cookie 失效） | `python tools/import_session.py --probe` 检查；`python tools/capture.py --interactive` 重新登录 |
| HTTP **429** `queue_timeout` | `max_concurrency: 1` 时排队超过 `rate_limit.max_wait_seconds` | 调大该值、降低调用频率；**不要并发压测** |
| HTTP **429** `rate_limit_error` | 上游限流 | 保持 `max_concurrency: 1`；稍后重试 |
| HTTP **504** `upstream_timeout` | 非流式等待整段生成超过 `upstream.nonstream_timeout`（当前 **90s**） | 改用 `stream: true`；或 `model.effort: no_think`；或适度调大（须小于客户端超时） |
| HTTP **500/502** `server_error` | 上游 5xx 或网络中断 | 看 `logs/upstream.log`；不要盲目重发（可能已扣额度） |
| `/v1/models` 返回 `data: []` + `x_warning` | 模型 ID 未确认（极少见，当前已确认 `hy4-preview`） | 抓包确认后在 profile 填 `models` |
| `没有捕获到流式帧` | 抓包期间没有真正点"发送" | 重跑 capture，按提示操作 |
| `storage_state 不存在` | 还没登录导出 | `python tools/capture.py --interactive` |
| 401 但浏览器里明明已登录 | cookie 域/过期问题 | `python tools/import_session.py --probe` 检查 |
| 控制台中文乱码 | PowerShell 代码页 | `chcp 65001` 并设 `$env:PYTHONIOENCODING='utf-8'`（文件内容是 UTF-8，不影响产物） |
| `playwright 未安装` / 浏览器缺失 | 依赖或浏览器未下载 | `pip install playwright==1.58.0 && python -m playwright install chromium` |
| 上游返回 403 / 空响应 | 可能命中风控 / TLS 指纹 | 把 `config.upstream.transport` 改成 `curl_cffi`（**0.14.0 已随 `requirements.txt` 安装**）再试 |

## 日志在哪、看什么

| 文件 | 内容 |
| --- | --- |
| `logs/server.log` | 服务生命周期、启动告警（含协议指纹变化） |
| `logs/request.log` | 每个 OpenAI 请求一行：`request_id`、模型、会话键、首 token 延迟、总耗时、finish_reason |
| `logs/upstream.log` | 上游调用：URL、状态码、耗时、请求体字段名（**值不写**，凭证脱敏） |
| `logs/error.log` | 仅 ERROR 及以上 |

所有日志都过 `RedactingFilter` + `RedactingFormatter`，凭证只会是 `****`。

## 流式相关

* 客户端收到 `data: {...}` 与结尾 `data: [DONE]`；中途错误会下发
  `{"error": {...}}` 事件，然后正常 `[DONE]` 收尾（不会 200 后突然断开）；
* 若你看到"首包 `{"role":"assistant"}` 之后再无内容"，说明上游或解析器有问题：
  看 `logs/upstream.log` 里 `event=upstream_stream` 的 `lines` 与 `first_line_ms`；
* `include_usage`（`stream_options.include_usage=true`）会额外给一个只含 usage 的 chunk。

## 上游协议变化（§43）

```text
WARNING: upstream protocol changed -> endpoint send_message: {...} -> {...}
```

处理顺序：

1. `python tools/capture.py --interactive --label after-change`
2. `python tools/diff_captures.py captures/after-change/requests.json`
3. 按 `diff_report.md` 判断是 URL / Header / Body / 认证 / 响应 哪一类变化
4. 只改 `protocol/current/profile.yaml`（必要时 `protocol/v2/`），不动 `app/` 代码
5. `python tools/healthcheck.py --direct --write-snapshot` 更新快照

## 我该把什么发出来求助（安全）

* 可以：`captures/<label>/summary.md`、`requests.json`（已脱敏）、`diff_report.md`、
  `logs/*.log`（已脱敏）；
* **不要**：`secrets/` 下任何文件、未脱敏的 HAR、浏览器导出的完整 Cookie。
