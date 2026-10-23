# 更新日志 / Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)；协议档案（`profile.yaml`）
的 `status` 变化视为**不兼容变更**。

## [1.0.0] - 2026-10-23

首个公开版本：**协议已抓包确认**（`protocol/current/profile.yaml: status: captured`），
从“协议未确认时只能 501”的阶段进入“核心对话能力可用”的阶段。

### Added

* **OpenAI 兼容端点**：`POST /v1/chat/completions`（流式 / 非流式）、`GET /v1/models`、
  `GET /v1/models/{id}`、`GET /v1/protocol`、`GET /health`（`?probe=true` 可选）。
* **流式 SSE**：`text/event-stream`、首包 `delta.role`、`stream_options.include_usage`
  时追加 usage chunk、结尾 `data: [DONE]`。
* **本地适配层**（上游只有单个 `prompt` 字符串，没有 `role`/`tools` 字段）：
  * `system`（人格）→ `系统：…` 拼接进 `prompt`；
  * 多轮历史 → `用户：…` / `助手：…`，超长时保留全部 system + 最近轮次；
  * `role="tool"` 结果 → `工具结果(<name>)：…`；
  * `tools` / `tool_choice` → **本地桥接**（渲染工具说明进 prompt，再把模型输出的
    `{"tool_calls":[…]}` 解析成标准 `tool_calls`，失败自动回退纯文本）。
* **并发限流**：`rate_limit.enabled` / `max_concurrency` / `max_wait_seconds`，
  排队超时返回 `429 queue_timeout` + `Retry-After`。
* **错误映射**：`501 protocol_unknown` / `401 auth_error` / `502 upstream_error` /
  `504 upstream_timeout`，全部为 OpenAI 风格错误体。
* **抓包与分析工具链**：`tools/capture.py`（CDP + 页面注入双通道）、
  `analyze_bundle.py`、`diff_captures.py`、`replay.py`、`import_session.py`、
  `healthcheck.py`、`smoke_chat.py`、`selfcheck.py`、`analyze_bundle.py`。
* **Windows 一键启动**：`一键启动.bat` / `一键登录抓包.bat` / `一键菜单.bat`
  （ASCII 壳 + `scripts/launcher.py`，规避 cmd.exe 的 UTF-8 批处理缺陷）。
* **文档**：`README.md`、`docs/architecture.md`、`docs/protocol.md`、
  `docs/protocol_captured.md`、`docs/authentication.md`、`docs/troubleshooting.md`、
  `docs/api_inventory.md`、`REPORT.md`、`DISCLAIMER.md`、`SECURITY.md`、
  `CONTRIBUTING.md`、`LICENSE`（非商业学习许可）。

### Changed

* 协议档案升级为 `captured`：域名 `api.hunyuan.tencent.com`、会话创建、消息 SSE、
  停止生成、会话列表、用户信息、模型列表均已确认；字段映射改为
  `stream.type_field` / `stream.type_map`（旧 `event_map` 已废弃）。
* 默认 `upstream.nonstream_timeout` 在 `config.yaml` 中为 **90s**（代码兜底默认 180s）。
* 抓包对 `Cookie` / `Authorization` / `token` / `sessionid` 等字段统一掩码。

### Fixed

* `app/api/chat.py`：移除未使用的 `uuid`、`redact_text` 导入。
* `app/api/chat.py`：修正并发槽位在“排队超时 / 任务取消”时可能泄漏的竞态
  （以 `_acquired` 标志兜底归还）。
* `app/main.py`：新增 `lifespan`，退出时 `await runtime.aclose()` 关闭上游 httpx 连接池。
* `app/main.py`：CORS 在 `allow_origins=["*"]` 时不再同时开启 `allow_credentials`
  （浏览器会拒绝该组合）。
* `tests/run_all.py`：补上 `test_tool_bridge` / `test_capture_cli_args` / `test_live_upstream`。
* `app/upstream/conversation.py`：会话缓存淘汰改为按**访问序号**（LRU），不再按
  `time.time()` 排序。Windows 上 Python < 3.13 的 `time.time()` 精度只有 ~15.6ms，
  同一 tick 内插入的条目时间戳相同，旧实现会退化成“按插入顺序”淘汰，把刚用过
  的会话踢掉（表现为多轮对话偶发丢上下文；CI `windows-latest / py3.11` 失败）。
  新增 `tests/test_conversation.py::test_eviction_follows_lru_even_with_coarse_clock`。
* `requirements.txt`：首行补 PEP 263 `# -*- coding: utf-8 -*-`。中文 Windows 的
  pip 默认按 GBK(cp936) 解码依赖文件，缺该声明时 `pip install -r requirements.txt`
  会直接报 `UnicodeDecodeError: 'gbk' codec can't decode byte ...`。

### Known limitations

以下能力**仍未抓包确认**，请求会得到明确的 `501 protocol_unknown`（绝不伪造成功）：

* 文件上传（`genUploadInfo` + COS 直传链路只到“存在”这一步）；
* 图片输入（消息体附件字段名未知）；
* 上游**原生** `tool_calls` 协议（当前用本地桥接替代）；
* 消息详情 / 历史补全接口。

其他限制：

* `conversation.mode: stateless`（默认）下每轮都会新建上游会话，网页端会看到
  “每轮一条新会话”，且不会累积上下文；
* 上游协议随时可能变更，变更后以 `501/502/504` 明确失败，不自动重试（避免重复扣额度）；
* 不支持多账号池、自动登录、验证码识别、代理池、压测。

### Security

* `secrets/`、`captures/`、`logs/`、`*.har`、`storage_state.json`、浏览器 Profile
  全部在 `.gitignore` 中；发布前请用 `git status --short` 复核。
* 默认 `server.api_keys: [changeme-please]` 仅为示例，请改为随机长字符串。

### License

非商业学习许可（Non-Commercial Study License），**禁止商业使用**；详见
[`LICENSE`](LICENSE) 与 [`DISCLAIMER.md`](DISCLAIMER.md)。
