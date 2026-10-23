# 架构（architecture.md）

## 数据流

```text
OpenAI 客户端 (Cherry Studio / AstrBot / curl ...)
        │  POST /v1/chat/completions   (stream 可为 true)
        ▼
app/api/chat.py        校验 → 会话解析 → 组装上游请求（模板来自 profile）
        │
        ▼
app/upstream/client.py  AIStudioClient（协议档案驱动，未确认即报错）
        │
        ├── transport.py   httpx（默认）/ curl_cffi（TLS 指纹备选）
        │                  · Cookie 注入（来自 secrets/storage_state.json）
        │                  · HTML 响应按键失败处理（防御性，见 docs/findings.md E9 的修正）
        │
        ├── chat.py        按 profile.request_schema.body_template 渲染请求体
        └── streaming.py   SSE 解析 + profile.stream.type_field/type_map 判别正文/思考
        │
        ▼
https://api.hunyuan.tencent.com/api/**（跨域；Origin=https://aistudio.tencent.com）
        │
        ▼
app/adapters/openai.py  ChatChunk → OpenAI 响应 / SSE chunk
        │
        ▼
OpenAI 客户端
```

## 模块职责

| 模块 | 职责 | 关键约束 |
| --- | --- | --- |
| `app/config.py` | 运行参数（端口、并发、日志、会话模式） | **不放协议事实** |
| `app/protocol/profile.py` | 协议档案：endpoint / body 模板 / 响应路径 / 流式映射 / 模型 ID | 每项带 `status`；`unknown` 一律抛错 |
| `app/upstream/transport.py` | HTTP 传输、Cookie 注入、错误映射、日志脱敏 | 不感知业务字段名 |
| `app/upstream/session.py` | storage_state / Cookie 头解析、过期判断、脱敏摘要 | 原始凭证只进 `secrets/` |
| `app/upstream/chat.py` | 按模板渲染请求体 | 模板未确认 → `ProtocolUnknownError` |
| `app/upstream/streaming.py` | `ChatChunk` 统一格式 + 可插拔解析器 | 解析器/映射未确认 → 报错 |
| `app/upstream/conversation.py` | OpenAI 请求 ↔ 上游 `conversation_id`（auto/explicit/stateless） | 带 TTL 与容量上限 |
| `app/upstream/upload.py` | 文件/图片上传 | **当前刻意不可用**（见文末） |
| `app/adapters/openai.py` | messages 转换、reasoning 策略、usage/finish_reason 归一化 | 字段名不猜，路径来自 profile |
| `app/api/*` | `/v1/chat/completions`、`/v1/models`、`/health`、`/v1/protocol` | 错误统一为 OpenAI 风格 |
| `app/security/redaction.py` | 脱敏（日志过滤器 + 格式化器 + 嵌套结构） | 全项目唯一脱敏入口 |
| `analyzer/*` | 浏览器自动化、双通道抓包、指纹、分类、差分、清单 | 只分析，不改上游 |
| `tools/*` | capture / diff / replay / import_session / healthcheck / smoke_chat / selfcheck | 面向人的 CLI |

## 双通道抓包（关键设计）

```text
通道 1：CDP
  Network.requestWillBeSent / responseReceived / dataReceived
  / loadingFinished / eventSourceMessageReceived / webSocketFrame*
   → 请求头、状态码、非流式响应体（getResponseBody）

通道 2：页面注入（analyzer/init_scripts.py）
  包裹 fetch / XMLHttpRequest / EventSource / WebSocket
  对 fetch 响应使用 ReadableStream.tee()，逐帧记录 SSE/分块数据
   → 解决 CDP 对流式 body 经常取不到的问题（抓包时必须开启，否则拿不到流式帧）
```

两条通道的记录按 `(url, method, 时间窗)` 合并，`captures/<label>/requests.json`
里既能拿到请求体，也能拿到流式原始帧。

## 会话映射（§19）

| 模式 | 行为 | 适用 |
| --- | --- | --- |
| `stateless`（**默认**） | 每次请求都新建上游会话（先调 `POST /api/new-portal/generate/id`），回答完即弃，**互不相关**；只发最新一条 prompt | 单轮 / 每问独立（防串味） |
| `auto` | 优先 `X-Session-Id`，其次 OpenAI `user` 字段，最后首条 user 消息指纹；命中则复用上游 `conversation_id` | 单机多轮 |
| `explicit` | 仅当请求带 `X-AIStudio-Conversation-Id` 时复用 | 多客户端隔离 |

`request_schema.history_mode` 决定 prompt 里放什么：当前抓包/实现为
**`full_history`**（由本代理把「system + 历史 + 当前问题」渲染进 `prompt`），
而不是依赖上游会话记忆。

> 默认 `stateless` + `full_history` ⇒ 仍然**每次新建上游会话**，
> 但 prompt 内可携带客户端发来的历史；`auto` 只是复用上游 `conversation_id`。
> `stateless` 下不读写本地会话缓存（`cache_size`/`ttl_seconds` 不生效）。

## 超时、并发与思考强度

上游 chat 接口**只有 SSE**，因此非流式请求是"等整段生成完再回一个 JSON"，
超时与排队必须显式处理，否则客户端只会看到"没有返回"：

| 配置 | 作用 |
| --- | --- |
| `model.effort` | 思考强度（`no_think`/`high`，取值来自抓包 `getChatConfig.thinkMode.options`）；`high` 会让单次生成变成长达数分钟 |
| `upstream.nonstream_timeout` | 非流式等待上限 → 超时 `504 upstream_timeout`（不再让客户端自己超时断连） |
| `rate_limit.max_wait_seconds` | 排队等待上限 → 超时 `429 queue_timeout` + `Retry-After` |

流程要点（`app/api/chat.py`）：

1. **先拿并发槽，再建上游会话**（`ensure_conversation` 在 `_slot` 内）——
   否则排队期间会在上游账号里留下空会话；
2. 流式：槽位在返回响应前获取、由生成器 `finally` + `BackgroundTask` 双重归还，
   客户端中途断开也不泄漏；
3. `reasoning.mode=fallback_text` 是**本地兜底策略**：仅当上游只回思考、正文为空时，
   才把思考当正文返回（平时仍走 `reasoning_content`）。

## 人格 / 历史 / 工具的本地适配

上游 chat body 只有一个 `prompt` 字符串（抓包确认），所以：

| 客户端给 | 本代理 | 代码 |
| --- | --- | --- |
| `system`（人格） | 渲染成 `系统：…` 拼到 prompt 最前 | `app/upstream/chat.py: render_messages_text()` |
| 多轮历史 / `role="tool"` | 渲染成 `用户：…` `助手：…` `工具结果(…)：…`；`model.history_max_chars` 截断时保 system + 最近轮次 | 同上 |
| `tools` / `tool_choice` | 桥接：说明渲染进 prompt -> 模型输出 JSON -> 解析成标准 `tool_calls`（`tools.mode: bridge`） | `app/adapters/tool_bridge.py` |

桥接失败（模型没按约定输出 JSON / 工具名不在声明里）**一律回退为普通文本**；
响应头 `x-aistudio-warning: tools_bridged`，日志字段 `tools_bridged` / `bridged_calls` /
`tool_calls_returned` 可观测。**这是本地约定，不代表上游协议**（`features.tool_calls` 仍为
`unknown`）。

## 错误与重试（§25 / §26）

* HTTP / 业务错误 → `AIStudio*` 异常 → OpenAI 风格 `{"error": {...}}`；
* 流**中途**出错：在流内下发 `error` 事件后正常 `[DONE]` 收尾，
  **不重发**（避免重复生成/重复扣额度）；
* `ProtocolUnknownError` → HTTP 501，明确告诉调用方"协议未确认"；
* `ProtocolChangedError` → HTTP 502（指纹不一致）。

## 为什么 upload.py 目前"不可用"

按项目规则（任务书 §42/§45）：没有抓包证据就**不猜测**字段名。
已知公开证据只有"`genUploadInfo` + COS SDK 直传"（证据 E7），
缺 `genUploadInfo` 请求体、临时密钥结构、登记接口、消息体附件字段名。
因此 `upload_file()` 会抛出带有"需要哪些证据"的 `ProtocolUnknownError`，
而不是实现一个可能把用户文件传到错误位置的功能。
