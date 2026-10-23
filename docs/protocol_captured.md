# 抓包确认后的协议（protocol_captured.md）

> 来源：`captures/baseline/`（2026-09-17，用户本人登录后抓包）
> 证据文件：`requests.json`（98 条记录）、`session.har`（含完整 SSE 响应体）、`summary.md`
>
> 本文只写**抓包证实**的内容；未抓到的仍是 `unknown`（见文末）。

---

## 0. 重要修正：阶段 1 的域名推断是错的

| 项目 | 阶段 1 推断（bundle 字符串） | 抓包实际（confirmed） |
| --- | --- | --- |
| API 域名 | ~~与页面同源 `https://aistudio.tencent.com/api/**`~~ | **`https://api.hunyuan.tencent.com/api/**`** |
| 原因 | 前端代码里写的是相对路径 `/api/...` | 运行时是**跨域**调用，Origin 为 `https://aistudio.tencent.com` |

教训（已写进流程）：**相对路径 ≠ 同源**。必须看真实请求的绝对 URL。

---

## 1. 认证（confirmed）

| 项目 | 值 |
| --- | --- |
| 载体 | Cookie（无 `Authorization`） |
| **必须 Cookie（最小集）** | `hunyuan_source`、`hunyuan_token`、`hunyuan_user` |
| 必须 Header | `chat_version: v1`、`credentials: include`、`mode: cors`、`X-AgentID: HunyuanDefault`、`X-Requested-With: XMLHttpRequest`、`Origin: https://aistudio.tencent.com`、`Referer: https://aistudio.tencent.com/` |
| Content-Type | **`text/plain;charset=UTF-8`**（body 实际是 JSON 字符串） |
| 签名 | 未发现 nonce/signature/timestamp（partially confirmed） |

> 注：`credentials: include` 是一个**自定义请求头**（不是 fetch 选项）。

---

## 2. 发送消息（核心，confirmed）

```
POST https://api.hunyuan.tencent.com/api/new-portal/chat/{conversationId}
Content-Type: text/plain;charset=UTF-8
→ 200 text/event-stream
```

请求体（逐字段来自抓包）：

```json
{
  "model": "gpt_175B_0404",
  "prompt": "<用户输入>",
  "plugin": "Adaptive",
  "displayPrompt": "<用户输入>",
  "displayPromptType": 1,
  "quickThink": false,
  "targetLang": "", "targetLangLabel": "", "sourceLang": "", "sourceLangLabel": "",
  "podcast": {"voices": []},
  "effort": "high",
  "multimedia": [],
  "agentId": "HunyuanDefault",
  "customParams": {"temperature": 0.9, "top_p": 1, "repetition_penalty": 1, "max_output_tokens": 65536},
  "supportHint": 1,
  "version": "v2",
  "chatModelId": "hy4-preview"
}
```

要点：

* **会话 ID 在 URL 路径里**（不是 body）；body 里不需要 conversation_id。
* `model: "gpt_175B_0404"` 是**固定值**（历史遗留字段），真正的模型选择是 `chatModelId`。
* 只发**最新一条** prompt（多轮上下文由上游按 conversation 维护）→ ~~`history_mode: prompt_only`~~
  **已改为 `full_history` + `prompt: "{messages_text}"`**（本代理的本地适配策略）：
  上游 body 里没有 role/system/tools 字段，因此 system（人格）与历史轮次由本代理渲染成
  文本拼进 `prompt`（`系统：…` / `用户：…` / `助手：…` / `工具结果(…)：…`）；
  `tools` 也走本地桥接（模型输出 JSON -> 标准 `tool_calls`）。详见
  `app/upstream/chat.py: render_messages_text()` 与 `app/adapters/tool_bridge.py`。
* **思考强度**由 `effort` 字段控制，取值来自抓包 `getChatConfig` 的
  `data.tabs.tabLanguage[].modelDetail.thinkMode.options[].value`：
  `no_think`（"更快完成回答，适合日常问答与轻量任务"）
  / `high`（"提供更充分的思考与推理"）。
  本代理由 `config.yaml: model.effort` 控制（默认 `no_think`，对齐网页端 "No Think"）；
  客户端传 OpenAI 的 `reasoning_effort` 时按 `minimal/low/medium → no_think`、
  `high → high` 覆盖。
  ⚠️ 实测把 `effort` 固定为 `high` 时，单次生成可达 **3~4 分钟**（SSE 5444 行），
  非流式客户端极易在等待中自己超时 → 表现为"回答完却没有返回"。

## 3. 创建会话（confirmed）

```
POST /api/new-portal/generate/id
Body: {"agentId":"HunyuanDefault","characterId":"","cloneId":"","forceNew":true}
Resp: {"id":"dalbtdk2c3mae40tp140","isNew":true}
```

会话 ID 形如 `dalbtdk2c3mae40tp140`（20 位小写字母数字）。

## 4. SSE 流式协议（confirmed）

完整事件生命周期：

```text
: speech_type / data: thought              ← TTS 提示（忽略）
data: {"type":"traceId","traceId":"..."}   ← 链路 ID（忽略）
data: {"type":"thought","title":"思考中...","msg":"好的","id":"<chatRecordId>"}   × N   ← 思考增量
data: {"type":"thought","title":"已深度思考","cost":10.117,"id":"<chatRecordId>"}       ← 思考结束+耗时
event: speech_type / data: text            ← 切换到正文
data: {"type":"text","msg":"我"}          × N   ← 正文增量
data: {"type":"meta","chatRecordId":"...","conversationId":"...","empty":false,"fileTooLong":false}
data: [DONE]
```

**关键**：正文与思考**共用 `msg` 字段**，必须用 `$.type` 判别：

```
type=text     → 正文          (msg)
type=thought  → 思考过程       (msg，title=思考中/已深度思考，cost=秒)
type=meta     → chatRecordId / conversationId
type=traceId  → 忽略
```

ID 规则：

| 名称 | 来源 | 形态 |
| --- | --- | --- |
| `conversation_id` | `POST /api/new-portal/generate/id` 的 `id` | `dalbtdk2c3mae40tp140` |
| `message_id`(chatRecordId) | thought 事件的 `id` / meta 事件的 `chatRecordId` | `dalbtdk2c3m0skb4ehv0` |

## 5. 其它已确认接口

| 功能 | Method | Path | 说明 |
| --- | --- | --- | --- |
| 重新生成 | POST | `/api/new-portal/chat/repeat/{conversation_id}` | 同为 SSE |
| 停止生成 | POST | `/api/new-portal/stop/chat/{conversation_id}` | JSON 响应 |
| 会话列表 | POST | `/api/new-portal/chat/conversation/list` | body `{limit,offset,modelTag:"all"}`，返回会话元信息（含 chatModelId/agentId） |
| 模型配置 | POST | `/api/new-portal/getChatConfig` | body `{chatModelId:"hy4-preview"}`，返回 modelId=327 与能力开关 |
| 用户信息 | GET | `/api/new-portal/getuserinfo` | 内容是 JSON（Content-Type 为 text/plain） |

## 6. 模型与能力（来自 getChatConfig / conversation/list）

| 项目 | 值 |
| --- | --- |
| 当前模型 | `hy4-preview`（`modelId=327`，`modelTag=text`，`agentId=HunyuanDefault`） |
| 思考模式 | `thinkMode.enable=true`，默认 High；请求体 `effort=high`、`quickThink=false` |
| 联网搜索 | `networkSearch.enable=true`；请求体 `plugin=Adaptive` |
| 文件上传 | `fileUpload.enable=true`，支持 ppt/pptx/doc/docx/pdf/txt/md，≤50MB/个，最多 10 个 |
| 图片输入 | `supportImageBackfill=false`（未抓包到图片输入 → unknown） |

## 6.5 OpenAI 映射与本地策略（哪些是上游事实、哪些是本代理约定）

> 这一节刻意把“**上游事实**”和“**本代理的本地策略**”分开写，
> 避免把本地适配误当成上游协议。

### 6.5.1 上游事实（来自抓包，不代表 OpenAI 语义）

| 上游实际 | 说明 |
| --- | --- |
| 只有一个 `prompt` 字符串 | 没有 `messages` / `role` / `system` / `tools` 字段 |
| 会话 ID 在 URL 路径 | `/api/new-portal/chat/{conversation_id}` |
| 响应只有 SSE | 没有非流式 JSON 响应体 |
| `$.type` 判别帧类型 | `text` / `thought` / `meta` / `traceId` |
| `effort` 控制思考强度 | `no_think` / `high`（来自 `getChatConfig.thinkMode.options`） |

### 6.5.2 本代理的本地映射（**不是**上游协议）

| OpenAI 侧 | 本代理处理 | 配置项 |
| --- | --- | --- |
| `messages[].role=system` | 渲染成 `系统：<内容>` 放到 `prompt` 最前 | `model.history_max_chars` |
| `messages` 历史 | 渲染成 `用户：…` / `助手：…`；超长保留全部 system + 最近轮次 | `model.history_max_chars` |
| `role="tool"` | 渲染成 `工具结果(<name>)：…` | 同上 |
| `tools` / `tool_choice` | **本地桥接**：工具说明渲染进 prompt，模型须输出 `{"tool_calls":[{"name":…,"arguments":{…}}]}`，再解析成标准 `tool_calls`（`arguments` 为 JSON 字符串，`finish_reason="tool_calls"`） | `tools.mode: bridge`｜`off` |
| `stream: false` | 本地聚合 SSE，最后回一个 JSON | `upstream.nonstream_timeout` |
| `reasoning_content` | `type=thought` → 扩展字段（非 OpenAI 标准） | `reasoning.mode` |
| `reasoning_effort` | `minimal/low/medium → no_think`，`high → high` | 覆盖 `model.effort` |
| `temperature` / `top_p` / `max_tokens` 等 | **默认不透传**（`passthrough_params: []`），只在日志/响应头告警 | profile `request_schema` |
| 思考兜底 | 仅当“只回思考、正文为空”时用思考当正文 | `reasoning.mode: fallback_text` |

桥接的安全约束：只接受**请求里声明过的工具名**（防止模型复述提示词里的示例 JSON
造成幻觉调用）；解析失败一律回退为普通文本，并在响应头给出
`x-aistudio-warning: tools_bridged`。

## 7. 仍未确认（保持 unknown）


| 项目 | 状态 | 原因 |
| --- | --- | --- |
| 文件上传协议（genUploadInfo 请求体/登记/附件字段） | unknown | 抓包期内未上传文件 |
| 图片输入 | unknown | 同上 |
| 工具调用 / 联网的显式事件 | unknown | 未触发 |
| 消息详情接口 | unknown | 未出现 |
| 签名/nonce/timestamp | partially confirmed（未发现） | 只在 bundle 里见过字样 |

## 8. 实测（2026-09-17）

```
python tools/smoke_chat.py --prompt "你好，请用一句话介绍你自己。"
[+] 上游：https://api.hunyuan.tencent.com
[+] 上游会话：dalc4bk2c3mcmbkd3hlg     ← 自动创建
[+] 首 token 延迟：1008.7 ms
[思考] 好的，用户让我用一句话介绍自己。…    ← reasoning 分流正确
我是混元，一个由腾讯开发的基层模型，专注于基础信息处理和逻辑响应。   ← 正文
[+] 总耗时：13128.1 ms，字符数：33   RC=0
```