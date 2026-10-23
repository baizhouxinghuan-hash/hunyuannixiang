# 协议（protocol.md）

> **当前状态：已抓包（`captures/baseline`，2026-09-17）**。
> `protocol/current/profile.yaml: status: captured`——“建会话 / 发消息（SSE）/ 重新生成 /
> 停止生成 / 会话列表 / 用户信息 / 模型配置”均已 `confirmed`。
> 仍未确认的只有 4 项：**文件上传、图片输入、上游原生 `tool_calls`、消息详情**。
>
> 本文说明：判定方法、抓包记录了什么、已确认的答案、以及上游变化时怎么改。

---

## 1. 判定方法：区分“发消息”与“读历史”（§8）

不要凭 URL 名字下结论，用**时间线因果**：

```text
t0  你点击"发送"
t1  出现请求 A（POST，body 里含你的原文）
t2  A 的响应开始持续输出内容（流式），或先返回后前端再拉取
t3  前端出现新的助手回答
↑ 只有满足 t1 < t2 ≤ t3 且 body 里能定位到你的原文，A 才是"发送消息"接口
```

反例（**不是**推理接口）：`conversation/list`、`.../detail`、`history` ——
它们出现在页面加载/切换会话时，body 里没有你的原文。

`tools/capture.py` 会把每个请求的 `seq`、method、URL、body、响应 content-type、
是否流式写进 `captures/<label>/requests.json`，并在 `summary.md` 给出分类候选与理由
（`analyzer/request_classifier.py`）。

## 2. 抓包记录了什么

```text
URL / Method / Request Headers / Request Body / Query
Response Headers / Response Body（非流式）/ Content-Type / Status
Timing / Initiator / stream_frames（流式原始帧）/ fingerprint
```

Cookie 与 Authorization 值**已脱敏**为 `****`；原始凭证只在
`secrets/storage_state.json`，不会写进 `captures/`。

## 3. 已确认的答案（对应早期问题清单）

| 问题 | 答案（evidence: `captures/baseline`） |
| --- | --- |
| 发送请求 URL | `POST https://api.hunyuan.tencent.com/api/new-portal/chat/{conversation_id}` |
| 建会话 | `POST /api/new-portal/generate/id`，body `{agentId, characterId, cloneId, forceNew}` → `{id, isNew}` |
| 重新生成 | `POST /api/new-portal/chat/repeat/{conversation_id}`（SSE，事件序列同发送） |
| 停止生成 | `POST /api/new-portal/stop/chat/{conversation_id}`（JSON） |
| 会话列表 | `POST /api/new-portal/chat/conversation/list`，body `{limit, offset, modelTag}` |
| 模型/能力 | `POST /api/new-portal/getChatConfig`，body `{chatModelId}` → `modelId` + `chatConfig` |
| 用户信息 | `GET /api/new-portal/getuserinfo` |
| Method | 聊天类全部 `POST`；只有 `getuserinfo` 是 `GET` |
| Content-Type（请求） | `text/plain;charset=UTF-8`，但 body 实际是 **JSON 字符串** |
| 必带 Header | `chat_version: v1`、`credentials: include`、`mode: cors`、`X-AgentID: HunyuanDefault`、`X-Requested-With: XMLHttpRequest`、`Origin/Referer: https://aistudio.tencent.com` |
| 必带 Cookie | `hunyuan_source`、`hunyuan_token`、`hunyuan_user`（无 `Authorization` 头） |
| 签名 / nonce / 时间戳 | 请求头与 body **均未发现** → `signed: false`（partially_confirmed） |
| 会话 ID | 在 URL 路径里（`/chat/{conversation_id}`），由 `generate/id` 返回 |
| 消息 ID | 流内字段 `chatRecordId` / `id`（`stream.event_map.message_id`） |
| 模型 ID | `chatModelId`（body 里的 `model: gpt_175B_0404` 是历史遗留固定值，**不代表模型**） |
| 流式 | **是**：`text/event-stream`，`$.type` 区分 `text` / `thought` / `meta`，以 `[DONE]` 结束 |
| 其他参数 | `effort`（`no_think` / `high`，取值来自 `getChatConfig.thinkMode.options`）、`plugin: Adaptive`、`customParams` |

### 请求体字段（`protocol/current/profile.yaml: request_schema.body_template`）

```yaml
model: "gpt_175B_0404"        # 历史固定值，真正模型是 chatModelId
prompt: "{messages_text}"     # ← 上游只有这一个字符串字段
displayPrompt: "{messages_text}"
displayPromptType: 1
plugin: "Adaptive"
effort: "{effort}"
chatModelId: "{model}"
agentId: "{agent_id}"
customParams: {temperature: 0.9, top_p: 1, repetition_penalty: 1, max_output_tokens: 65536}
version: "v2"
```

## 4. 流式解析

```text
speech_type(thought) → traceId → thought × N → thought(已深度思考, cost)
                    → speech_type(text) → text × N → meta → [DONE]
```

* `stream.type_field: "$.type"`、`stream.type_map: {text: text, thought: reasoning, meta: meta}`
  —— 正文与思考**共用 `$.msg`**，必须靠 `type` 判别；
* `speech_type` 只是 TTS 提示事件 → `event_name_map: {speech_type: ignore}`；
* `$.conversationId` / `$.chatRecordId` 用于会话与消息标识；
* 上游**没有非流式响应体**：`stream: false` 时由本地聚合 SSE 再返回一个 JSON
  （因此 `upstream.nonstream_timeout` 会直接影响非流式请求）。

> 早期文档里的 `stream.event_map` 是“路径候选表”，已由 `type_field` + `type_map`
> 取代；`event_map` 现在只保留 `message_id` / `conversation_id` / `usage` 等辅助路径。

## 5. 本地适配策略（不是上游行为）

上游 body 只有 `prompt` 一个字符串，没有 `role` / `system` / `tools` 字段。因此：

| 客户端输入 | 本代理处理 |
| --- | --- |
| `system`（人格） | 渲染成 `系统：…` 放在 prompt 最前 |
| 多轮历史 | 渲染成 `用户：…` / `助手：…`，超长时保留全部 system + 最近轮次 |
| `role="tool"` | 渲染成 `工具结果(<name>)：…` |
| `tools` / `tool_choice` | 本地桥接：工具说明渲染进 prompt，解析模型输出的 JSON 为 `tool_calls` |
| 采样参数 | 只透传 profile `passthrough_params` 列出的；其余忽略并告警 |

## 6. 上游变化时（§37 / §43）

* 启动时比对 `protocol/current/schema_fingerprint.json`，不一致会打印
  `WARNING: upstream protocol changed` 并出现在 `/health`；
* 排查顺序：`URL → Header → Body → Authentication → Response`；
* 通常只改 `protocol/`，不需要动 `app/` 代码：

```bash
python tools/capture.py --interactive --label after-change
python tools/diff_captures.py captures/after-change/requests.json --out diff_change.md
python tools/healthcheck.py --direct --write-snapshot
```

* 新增能力时请同步更新：`profile.yaml` 的 `status/evidence`、`docs/protocol_captured.md`、
  `CHANGELOG.md`，并补一个离线测试（`tests/test_stream.py` / `test_adapter.py`）。
