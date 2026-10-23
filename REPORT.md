# 项目汇报（REPORT.md）

> [!WARNING]
> **§1–§20 是阶段 1（纯公开 bundle 静态分析）的历史快照，不要当作当前状态。**
> 真实抓包（`captures/baseline`，2026-09-17）已把核心协议升级为 `confirmed`，
> 并**推翻了其中两条静态推断**（API 域名、流式机制）。
>
> **当前有效结论**：
> * [`docs/protocol_captured.md`](docs/protocol_captured.md) —— 抓包确认的端点 / 请求体 / SSE 生命周期；
> * [`protocol/current/profile.yaml`](protocol/current/profile.yaml) —— 机器可读、带 evidence 的协议事实；
> * [`docs/protocol.md`](docs/protocol.md) —— 判定方法与已确认答案；
> * 服务运行时：`GET /v1/protocol`、`GET /health`。
>
> 任务书 §44 要求的 20 项汇报。**状态严格区分**：
> `confirmed`（有直接证据）/ `partially confirmed` / `inferred`（仅推断） / `unknown`（未知）。

## 已抓包回填（Batch C，2026-09-17）

> 完整协议见 **[docs/protocol_captured.md](docs/protocol_captured.md)**；证据在 `captures/baseline/`。
> 本节是**最新状态**；下面 §1–§20 是阶段 1 的侦察快照，其中“API 域名”一项**已被抓包修正**。

### 关键修正（阶段1 推断 → 抓包事实）

| 项目 | 阶段 1 | 抓包确认 |
| --- | --- | --- |
| API 域名 | ~~与页面同源 `aistudio.tencent.com/api`~~ | **`https://api.hunyuan.tencent.com/api`**（跨域，Origin=aistudio） |
| 发送消息 | ~~`POST /api/new-portal/chat`~~ | **`POST /api/new-portal/chat/{conversation_id}`**（会话 ID 在 URL 路径） |
| 流式 | ~~unknown（bundle 里 0 处 event-stream）~~ | **SSE，`text/event-stream`，`data: [DONE]` 结束** |
| 正文/思考 | ~~unknown~~ | **同一 `msg` 字段，靠 `$.type` 区分：`text`=正文，`thought`=思考** |
| 创建会话 | ~~unknown~~ | **`POST /api/new-portal/generate/id`** → `{id,isNew}` |
| 必须 Cookie | ~~unknown~~ | **`hunyuan_source` / `hunyuan_token` / `hunyuan_user`（仅 3 个）** |
| 模型 | ~~unknown~~ | **`hy4-preview`（modelId=327，agentId=HunyuanDefault）** |

### 20 项状态速览（本轮之后）

| # | 项目 | 状态 |
| --- | --- | --- |
| 1 | API 域名 | confirmed：`api.hunyuan.tencent.com` |
| 2 | 模型列表 | confirmed：`hy4-preview`（`/v1/models` 可直接列出） |
| 3 | 创建会话 | confirmed：`POST /api/new-portal/generate/id` |
| 4 | 发送消息 | confirmed：#7 全部字段来自抓包 |
| 5 | 流式 | confirmed：SSE + `type` 判别（见 §7） |
| 6 | 停止生成 | confirmed：`POST /api/new-portal/stop/chat/{id}` |
| 7 | 历史会话 | confirmed：`POST /api/new-portal/chat/conversation/list` |
| 8 | 文件上传 | unknown（未抓包到上传动作） |
| 9 | 图片输入 | unknown（`supportImageBackfill=false`） |
| 10 | 认证 | confirmed：Cookie（无 Authorization） |
| 11 | 必须 Cookie | confirmed：仅 3 个 hunyuan_* |
| 12 | 必须 Header | confirmed：`chat_version/credentials/mode/X-AgentID/X-Requested-With/Origin/Referer` |
| 13 | conversation_id | confirmed：来自 `generate/id`，形如 `dalbtdk2c3mae40tp140` |
| 14 | message_id | confirmed：SSE 的 `chatRecordId`/`thought.id` |
| 15 | SSE 格式 | confirmed：`type=text/thought/meta/traceId` + `[DONE]` |
| 16 | OpenAI 映射 | 已实现（非流式由本地聚合，因为上游只有 SSE） |
| 17 | 模型 | confirmed：`hy4-preview` |
| 18 | 已测试 | 见 §18（本轮：194 passed / 4 skipped / 0 failed） |
| 19 | 未确认 | upload / image_input / tool_calls / message_detail |
| 20 | 已知限制 | 需登录会话；上游只有流式；上传未实现 |

### 实测证据（真实调用上游一次）

```text
[+] 上游：https://api.hunyuan.tencent.com
[+] 上游会话：dalc4bk2c3mcmbkd3hlg     ← 自动创建
[+] 首 token 延迟：1008.7 ms
[思考] 好的，用户让我用一句话介绍自己。…
我是混元，一个由腾讯开发的基层模型，专注于基础信息处理和逻辑响应。
[+] 总耗时：13128.1 ms，字符数：33
```

## 运行信息（本地服务）

| 项目 | 值 |
| --- | --- |
| 一键启动（推荐） | 双击 **`一键启动.bat`**（登录抓包用 `一键登录抓包.bat`，工具箱 `一键菜单.bat`） |
| 监听地址 | `http://127.0.0.1:1010`（`config.yaml: server.port = 1010`） |
| OpenAI Base URL | `http://127.0.0.1:1010/v1` |
| 健康检查 | `http://127.0.0.1:1010/health` |
| 协议摘要 / Swagger | `http://127.0.0.1:1010/v1/protocol`、`/docs` |
| 端口覆盖优先级 | `--port` > 环境变量 `AI_STUDIO_PORT` > `config.yaml` > 代码默认值（均为 1010） |
| 自检临时端口 | `tools/selfcheck.py` 默认在 **8123** 临时起实例（避免与运行中的 1010 冲突） |
| bat 实现方式 | bat 是**纯 ASCII 壳**，中文界面与逻辑在 `scripts/launcher.py`（规避 cmd 的 UTF-8 缺陷） |

## 本轮修复（全部有实测证据，并已加回归测试）

| # | 问题 | 现象 | 修复 |
| --- | --- | --- | --- |
| 1 | **文档命令跑不通** | 照抄 README 的 `python tools/capture.py --interactive --har --label baseline` → `error: unrecognized arguments: --interactive` | `tools/capture.py` 增加 `--interactive` 与 `--har`（与 `--no-har` 互斥）；`tools/{replay,import_session,healthcheck,smoke_chat}.py` 抽出 `build_parser()`；新增 `tests/test_capture_cli_args.py` 锁死"文档命令必须可解析" |
| 2 | **pip 依赖冲突** | `pip install -r requirements.txt` → `ResolutionImpossible`（`sse-starlette 3.3.3` 要 `starlette>=0.49.1`，`fastapi 0.115.6` 只允许 `<0.42.0`） | 移除**代码里 0 引用**的 `sse-starlette / httpx-sse / python-multipart / jinja2`，不再固定 `starlette`；现在 pip 秒过无冲突 |
| 3 | **bat 中文被 cmd 拆成命令** | `'退出' is not recognized`、`[!]` 显示成 `[]`、`'号块' is not recognized` | 三个入口 bat 改为**纯 ASCII 壳** + `scripts/launcher.py` 承载中文界面；单测断言 bat 无任何非 ASCII 字节 |
| 4 | **测试污染真实目录** | `tests/test_server.py` 把假会话写进了项目 `secrets/storage_state.json` | 测试的 `storage_state` 一律指向 `tmp_path`；已删除被污染的假会话文件 |
| 5 | **`/health` 在真实档案下 500** | 未登录且存在 unknown_features 时 `KeyError: 'auth_error'` | 重写状态判定逻辑（`degraded` / `ok_with_unknown_protocol`）；新增回归测试 |
| 6 | **端到端自检发现的编码 bug** | `x-aistudio-warning` 头含中文 → `latin-1` 编码异常（HTTP 500） | 告警头改为 ASCII 码（`tools_not_passed,params_ignored:top_p`），中文说明只进日志 |
| 7 | **非流式"回答完却没有返回"** | 请求体里 `effort` 被写死为 `"high"`（阶段1 回填值）→ 上游深度思考，单次生成 **28s ~ 255s**（实测 SSE 5444 行）；客户端在自己超时时间内收到 0 字节 → 放弃；服务端日志仍是 `200 OK`。客户端重试期间（并发=1）还会在上游留下**空会话** | ① `model.effort` 可配，默认 `no_think`（对齐网页端 No Think），并支持 OpenAI `reasoning_effort` 映射；② 非流式加 `upstream.nonstream_timeout` → `504 upstream_timeout`（明确报错，不再让客户端自己超时）；③ `ensure_conversation` 移入并发槽内（排队不再建空会话）；④ `rate_limit.max_wait_seconds` → `429 queue_timeout` + `Retry-After`；⑤ 新增 `reasoning.mode: fallback_text`（仅正文为空时用思考兜底）；⑥ 日志加 `reasoning_chars / queued_ms / effort / tools_requested / fallback_text_used` |
| 8 | **协议指纹快照过期** | `/health` 持续误报 `WARNING: upstream protocol changed`（快照仍是阶段1 的同源猜测值） | 按抓包回填后的档案重写 `protocol/current/schema_fingerprint.json` |
| 9 | **人格（system）与多轮历史被丢弃** | AstrBot 把 persona 作为 `system` 消息下发（`openai_source.py:975-976`），但本代理 `prompt_only` 只取最后一条 user 消息 → 实测 `PROMPT='你好'`、`SYSTEM_CARRIED=False`；人格、历史、`role="tool"` 结果全都进不了 prompt（群里"小白"人设消失） | profile 改 `full_history` + `prompt: "{messages_text}"`；新增 `render_messages_text()`（渲染 `系统：/用户：/助手：/工具结果(…)：`）与 `model.history_max_chars` 截断（保 system + 最近轮次）；真实请求实测：带 `system="你是小白…必须带喵"` → 回答 `（耳朵轻轻抖动…）主人好呀喵~小白等主人很久了喵！` |
| 10 | **tool_calls 不可用（function-calling 不触发）** | 客户端（AstrBot/Cline）每轮发 `tools`，上游 body 没有工具字段 → 只能丢弃 + `x-aistudio-warning: tools_not_passed`，Agent 永远收不到 `tool_calls` | 新增**本地工具桥接** `app/adapters/tool_bridge.py`（`config.yaml: tools.mode: bridge`）：工具说明（name/description/JSON Schema）渲染进 prompt → 解析模型输出的 JSON → 返回**标准 `tool_calls`**（`arguments` 为 JSON 字符串 / `finish_reason=tool_calls`）；只接受已声明的工具名（防幻觉）、解析失败回退普通文本；`tools.mode: off` 保留原"诚实告警"行为。真实请求实测：`warning=tools_bridged`、`finish_reason=tool_calls`、`get_weather({"city":"北京"})` |
| 11 | **排查困难：不知道客户端到底发了什么** | "人格/历史有没有发过来"只能靠猜，定位一次要贴整段 prompt | ① 每次响应加 `x-aistudio-messages: <总条数>/<system条数>/<历史条数>`；② 日志加 `messages_count / system_messages / system_chars / history_messages / history_chars / tools_count / prompt_chars`；③ `displayPrompt` 改用 `{messages_text}` → **AI Studio 网页端那条消息里能直接看到拼给模型的完整文本**。真实请求实测：`x-aistudio-messages=4/1/2`，模型答出历史里的「蓝色」（人格+多轮同时验证） |

---

## 0. 一句话结论（阶段 1 静态分析 —— 已被抓包推翻，仅存档）

> 下面三句**全部不成立**，保留原文是为了记录“静态推断错在哪”：
> 真实域名是 `api.hunyuan.tencent.com`（跨域），发送接口是
> `/api/new-portal/chat/{conversation_id}`，流式机制是 **SSE**。

**网页端 API 与页面同源**（`https://aistudio.tencent.com/api/**`），
**不是**任务书预设的 `api.hunyuan.tencent.com`；
"发送消息"接口是 `/api/new-portal/chat`（**推断**）；
流式机制在全仓 **0 处** `text/event-stream` 字面量，**必须抓包定性**；
因此在协议回填前，本项目对聊天请求返回 **501 protocol_unknown**（不猜、不伪造）。

## 1. AI Studio 当前实际 API 域名（历史快照，⚠️ 结论已被抓包修正）

| 项 | 值 | 状态 | 证据 |
| --- | --- | --- | --- |
| API 域名 | `https://aistudio.tencent.com`（与 SPA 同源） | **confirmed** | E3；`frontend/summary.md` |
| API 前缀 | `/api` | **confirmed** | E3（135 条去重路径） |
| `api.hunyuan.tencent.com` | **前端 bundle 中 0 次出现** | **confirmed** | E3 |
| 静态资源域名 | `cdn-portal.hunyuan.tencent.com` | **confirmed** | E2 |
| 上传对象存储 | `hunyuan-*-1258344703.cos.ap-guangzhou.myqcloud.com` 等 | inferred | E7 |

## 2. 模型列表接口

| 项 | 值 | 状态 |
| --- | --- | --- |
| 候选 1 | `GET /api/models` | inferred（E4） |
| 候选 2 | `GET /api/agent/model/list` | inferred（E4） |
| 实际返回结构 | 未知 | **unknown** |
| `/v1/models` 当前输出 | `{"object":"list","data":[],"x_warning":"..."}` | confirmed（本地实测） |

## 3. 创建会话接口

| 项 | 值 | 状态 |
| --- | --- | --- |
| 是否存在独立建会话接口 | 未知（很可能是**首条消息自动建会话**） | **unknown** |
| 相关候选 | `/api/new-portal/chat/conversation/update`、`/api/user/agent/conversation/continue` | inferred |
| 本地行为 | `create_conversation()` 抛 `ProtocolUnknownError` | confirmed |

## 4. 发送消息接口（核心）

| 项 | 值 | 状态 |
| --- | --- | --- |
| URL | `POST /api/new-portal/chat` | **inferred**（E4，来自 bundle endpoint 常量表） |
| Content-Type | `application/json` | inferred（E5：统一 wrapper `{url, method, data}`） |
| Request Body | 未知 | **unknown** |
| 必须 Header | 未知（疑似只需浏览器基础头） | **unknown** |
| 必须 Cookie | 未知 | **unknown** |
| 会话 ID / 消息 ID | 字段名与生成规则未知 | **unknown** |
| 是否真正触发推理 | **尚未用时间线验证** | **unknown** |

**没有把它写成 confirmed 的原因**：任务书 §8 要求用时间线证明
"请求里带原文 → 之后出现流式/新回答"，这一步必须由真实抓包完成。

## 5. 流式接口

| 项 | 值 | 状态 |
| --- | --- | --- |
| `text/event-stream` 字面量 | index/hy-web/utils/6473 全为 **0** | **confirmed**（E8） |
| `EventSource` 使用 | 仅 vendor chunk 与 monitor 出现 | unknown（不能证明聊天在用） |
| `ReadableStream` / `getReader` | index 出现 10 / 7 次，utils 16 / 2 次 | unknown（同上） |
| WebSocket 聊天通道 | 仅 vendor/monitor 出现 | **unknown** |
| 本地实现 | 解析器可插拔（sse / ndjson / json_lines）+ profile 事件映射 | confirmed（离线测试通过） |

## 6. 停止生成接口

| 项 | 值 | 状态 |
| --- | --- | --- |
| URL | `POST /api/new-portal/stop/chat` | inferred（E4） |
| 请求体 | 未知 | **unknown** |
| 本地行为 | endpoint 或 body 模板未确认 → `ProtocolUnknownError` | confirmed |

## 7. 历史会话接口

| 项 | 值 | 状态 |
| --- | --- | --- |
| 候选 1 | `POST /api/user/agent/conversation/v1/detail` | inferred（E4） |
| 候选 2 | `/api/new-portal/user/agent/conversation/continue` | inferred（E4） |
| 如何区分"读历史"与"发消息" | 分类器把"POST + 会话字段但无对话字段"判为 `history`，并保留 `send_message` 作为备选 | confirmed（规则已实现并测试） |

## 8. 文件上传接口

| 项 | 值 | 状态 |
| --- | --- | --- |
| URL | `POST /api/new-portal/chat/resource/genUploadInfo` | inferred（E4/E7） |
| 上传方式 | 返回临时密钥（`TmpSecretId`…）后由 `cos-sdk` 直传 COS | inferred（E7） |
| 请求体 / 密钥结构 / 登记接口 / 消息体附件字段 | 全部未知 | **unknown** |
| 本地实现 | **刻意不可用**，返回带"需要哪些证据"的 `ProtocolUnknownError` | confirmed |

## 9. 图片接口

| 项 | 值 | 状态 |
| --- | --- | --- |
| 是否支持图片输入 | 未知 | **unknown** |
| 图像能力接口（发现） | `/api/image/{clarity,edit/asset/save,elimination,outpainting,removewatermark,style,search}` | inferred |
| 本地行为 | 请求含 `image_url` 且能力未确认 → `ProtocolUnknownError` | confirmed（有单测） |

## 10. 认证机制

| 项 | 值 | 状态 |
| --- | --- | --- |
| 载体 | 同站 Cookie（`fetch(..., {credentials:"include"})`） | partially confirmed（E6） |
| `Authorization` | 未发现（仅 COS SDK 签名用） | partially confirmed |
| 签名 / nonce / timestamp | 前端有 `signature`/`nonce`/`timestamp` 字样，**是否参与鉴权未知** | **unknown** |
| 未登录表现 | `/api/*` → **200 + text/html（SPA）** | **confirmed**（E9） |
| 本地实现 | Cookie 注入 + HTML→401 判定 + 上游 Set-Cookie 回写 | confirmed |

## 11. 必须 Cookie

**unknown**。已知：需要登录态 cookie（E6/E9）；具体最小集合需抓包确认。
本地 `session.py` 支持 storage_state / Cookie 头两种来源，并会自动剔除已过期项。

## 12. 必须 Header

**unknown**。本地默认发送浏览器化基础头（`User-Agent`、`Accept`、`Accept-Language`、
`Referer`、`Origin`、`Content-Type`），可被 profile 的 `auth.required_headers` 覆盖。
这些基础头属于**推断**（网页端是浏览器环境），抓包后会收敛为最小集。

## 13. conversation_id 规则

**confirmed**（抓包）：`conversation_id` 由 `POST /api/new-portal/generate/id` 返回
（`$.id` / `$.data.id`），发送消息时放在 **URL 路径**里；本地映射层（任务书 §19）三种模式：

| 模式 | 行为 | 状态 |
| --- | --- | --- |
| `stateless`（**默认**） | 每次请求都新建上游会话，本次回答与上一次无关 | confirmed（有单测） |
| `auto` | `X-Session-Id` → `user` → 首条消息指纹 作为会话键，命中复用 | confirmed（有单测） |
| `explicit` | 只用 `X-AIStudio-Conversation-Id` | confirmed（有单测） |

## 14. message_id 规则

**unknown**。本地只在响应/流事件里按 profile 配置的路径提取（`message_id_paths`），
不生成、不猜造；提取不到就是 `None`。

## 15. SSE 格式

**unknown**（禁止猜测）。已知事实与本地能力：

| 项 | 值 |
| --- | --- |
| 全仓 `text/event-stream` | 0 次（confirmed，E8） |
| 本地解析器 | `SSEParser`（支持 `event:` / `data:` / `id:` / `retry:` / 注释 / 多行 data / 空行分组）+ `NDJSONParser` + `JSONLinesParser` |
| 事件语义映射 | profile `stream.event_map` / `event_name_map`（未填 → 501，不猜） |
| 格式识别工具 | `analyzer/response_parser.py` 输出 format/置信度/证据/事件序列/候选字段路径 |
| 离线验证 | mock 上游的 SSE 全链路（首包→增量→finish→[DONE]）通过 |

## 16. OpenAI API 映射

| OpenAI | 本项目实现 | 状态 |
| --- | --- | --- |
| `POST /v1/chat/completions`（非流式） | 已实现（含 `usage`、`finish_reason`、`reasoning_content`） | confirmed（离线测试通过） |
| `POST /v1/chat/completions`（`stream:true`） | SSE：首包 role → 增量 → finish → `[DONE]` | confirmed（离线测试通过） |
| `GET /v1/models` | 已实现（未确认时返回空 + `x_warning`，不编造） | confirmed |
| `GET /health` / `GET /v1/protocol` | 已实现（协议指纹、未知项列表、上传能力状态） | confirmed |
| `system` 消息 | 默认折叠进 user（可配 `as_message`） | confirmed |
| `tool_calls` | 标准结构 + **本地桥接**可用（`tools.mode: bridge`，真实请求实测返回 `finish_reason=tool_calls`）；**上游原生工具协议仍未确认** | 桥接 confirmed / 原生 unknown |
| `reasoning` | `passthrough` / `drop` / `as_text` 三种策略 | confirmed |
| 错误映射 | 401/403/404/408/409/413/422/429/5xx + 业务 `{code,message}` + `protocol_unknown`→501 | confirmed（有单测） |
| 限流 | `asyncio.Semaphore(max_concurrency)`，流式全程持锁 | confirmed（有单测） |
| 不重复扣额度 | 区分"未发送/已发送但响应丢失/部分接收/已完成"，只对"确定未发送"才允许重试；流中断**不重发** | confirmed |

## 17. 当前支持模型

| 项 | 值 | 状态 |
| --- | --- | --- |
| 已确认的模型 ID | **无** | unknown |
| `/v1/models` 输出 | 空数组 + `x_warning` | confirmed |
| 本地映射机制 | `profile.models`（OpenAI 名 → 上游 ID）+ `config.model.mapping` 覆盖 | confirmed |

## 18. 当前已测试功能（离线，全部通过）

```
python -m pytest        ->  194 passed, 4 skipped（共收集 198 项）
python -m tests.run_all ->  74 通过 / 46 跳过（依赖 pytest fixture）/ 0 失败
```

（4 个 skipped 是 `tests/test_live_upstream.py` 的真实上游用例，
在设置 `AI_STUDIO_LIVE=1` 且 profile 回填前一律跳过——不会伪造结果。）

| 测试文件 | 覆盖 |
| --- | --- |
| `test_redaction.py` | 凭证脱敏、幂等、日志过滤器/格式化器、抓包产物无原始 cookie |
| `test_profile.py` | 协议档案状态严格性、模板渲染、指纹与变化检测 |
| `test_stream.py` | SSE/NDJSON 解析、事件映射、JSON 路径、空流报错 |
| `test_adapter.py` | messages 转换、reasoning 三模式、finish_reason/usage 归一化、tool_call 包装、passthrough 白名单 |
| `test_errors.py` | 13 个状态码映射、HTML→未登录、`to_openai_error` 形状 |
| `test_capture_cli_args.py` | **文档命令必须可解析**（曾出现 `--interactive/--har` 不存在）+ bat 纯 ASCII 回归 + `scripts/launcher.py` 单测 |
| `test_conversation.py` | auto/explicit/stateless、TTL、容量淘汰 |
| `test_auth.py` | storage_state 解析、过期过滤、脱敏摘要、登录态探测（含 HTML 回退） |
| `test_models.py` | 模型映射、未确认时不出模型 |
| `test_chat.py` | 客户端真链路（上游为 mock）：非流式/流式/reasoning/401；真实档案下**发请求前**即报错 |
| `test_analyzer.py` | 指纹、分类、SSE 识别、候选路径、差分与分类、清单渲染、重放选帧 |
| `test_server.py` | `/health`、`/v1/models`、非流式、流式、多轮（小明）、429/500/HTML→401、501、限流、API Key、告警头、会话模式（stateless 每次新建 / auto 复用）、**effort 映射、非流式超时 504、排队超时 429、fallback_text 兜底、人格折叠、历史折叠、工具桥接（含流式 delta 与 off 模式）** |
| `test_tool_bridge.py` | 工具说明渲染、`tool_choice` 三态、JSON 解析（整段/代码块/嵌套/数组/多调用）、幻觉调用防护、失败回退 |
| `test_tools_cli.py` | capture/diff/replay/import_session/healthcheck CLI 冒烟 |
| `test_live_upstream.py` | 真实上游用例（默认 skip，`AI_STUDIO_LIVE=1` 且 profile 已回填才运行） |

本地实测验收（`python tools/selfcheck.py`，2026-09-18，profile 已 confirmed）：

```
[i] 上游：https://api.hunyuan.tencent.com（会话模式：stateless）
[i] 已启用本地鉴权（server.api_keys 非空）：/v1/* 请求会带上 Authorization 头
[i] profile 已由抓包确认：聊天检查将真实调用上游（消耗额度）
[PASS] GET /health                            status=ok
[PASS] GET /v1/models                         data=[hy4-preview]
[PASS] POST /v1/chat/completions              200 真实回答（latency 3582ms，effort=no_think，reasoning_chars=0）
[PASS] POST /v1/chat/completions?stream=true  200 text/event-stream + [DONE]
[PASS] GET /v1/protocol                       摘要 + confirmed_endpoints（7 个）
合计：5 通过 / 0 失败
```

**`effort` A/B 实测**（同一 prompt「用一句话解释什么是 HTTP 缓存。」）：

```
effort=no_think  耗时=  6464.7 ms  正文字数=  86  思考字数=    0
effort=high      耗时= 18188.4 ms  正文字数= 109  思考字数=  676
```

（`high` 在带 tools 的复杂请求上更极端：实测 **198s / 255s**，SSE 5444 行 —— 这就是
"非流式客户端等不到返回"的根因；修复后默认 `no_think`。）

## 19. 未确认功能（当前 UNKNOWN 清单）

`/health` 与 `/v1/protocol` 会**实时**列出这份清单。以
`protocol/current/profile.yaml`（`status: captured`）为准，当前只剩：

```text
endpoint:upload            # 文件上传链路（genUploadInfo 请求体/登记接口/附件字段）
endpoint:message_detail    # 消息详情 / 历史补全接口
feature:image_input        # 图片输入（supportImageBackfill=false，未抓包）
feature:tool_calls         # 上游原生工具调用协议（当前用本地桥接替代）
response_schema            # 上游只有 SSE，没有非流式响应体可抓
```

另有 `partially_confirmed`（不是 unknown，但也没有直接证据）：

```text
auth.signed                # 请求头/body 均未发现 nonce/signature/timestamp
feature:multi_turn         # 上游请求只带最新 prompt，多轮由本地渲染
feature:file_upload        # 仅看到 getChatConfig.fileUpload.enable=true
feature:web_search         # 仅看到 inputControl.networkSearch.enable=true + plugin=Adaptive
```

仍未定位到候选的功能：消息编辑、清空/删除会话的精确端点、独立的联网搜索接口、
用量/Token 展示接口、采样参数（temperature 等）的上游字段名。

## 20. 已知限制

1. **必须由你本人登录**：抓包需要扫码/短信登录，本项目不参与、不绕过登录。
2. **默认 `stateless`**：每轮新建上游会话，网页端会话列表会不断增加；
   想要上下文请改 `conversation.mode: auto`（历史仍由本地渲染进 `prompt`）。
3. **未确认能力一律 501**：文件上传 / 图片输入 / 原生 `tool_calls` / 消息详情，
   不会伪造实现。
4. **上游协议易变**：字段/域名/风控随时可能变化；变化后以 `501/502/504` 明确失败，
   并以 `upstream protocol changed` 告警，需重新抓包回填 profile。
5. **非流式是“聚合 SSE”**：`effort=high` 时单次可达 3~4 分钟（实测 198s/255s），
   非流式极易超时；默认 `effort=no_think`，并建议客户端用流式。
6. **单并发限制**：`max_concurrency: 1` 是刻意保守值（网页端本就单并发），
   不适合压测或多账号池。
7. **TLS 指纹**：若上游启用指纹风控，可切 `upstream.transport: curl_cffi`
   （已内置依赖，但**未在真实风控场景验证**）。
8. **Windows 控制台**：管道/重定向时中文可能显示乱码（文件内容正确），
   建议 `chcp 65001` + `PYTHONIOENCODING=utf-8`。

---

## 交付物清单（Batch A）

| 类别 | 内容 |
| --- | --- |
| 协议档案 | `protocol/current/profile.yaml`（E 证据 + 状态标注）、`protocol/current/schema_fingerprint.json`（自动生成） |
| 分析工具 | `analyzer/`（browser、network_capture、init_scripts、fingerprint、request_classifier、classifier、response_parser、endpoint_discovery、diff、parser、capture） |
| CLI | `tools/`（analyze_bundle、capture、diff_captures、replay、import_session、healthcheck、smoke_chat、selfcheck） |
| 服务 | `app/`（api、upstream、adapters、protocol、security）+ `run.py` + `start.bat`/`start.sh` |
| 测试 | `tests/`（194 passed / 4 skipped，共 198 项）+ mock 上游 + 合成抓包 + 无 pytest 兜底运行器 + bat 纯 ASCII 回归 |
| 文档 | `README.md`、`REPORT.md`、`CHANGELOG.md`、`DISCLAIMER.md`、`SECURITY.md`、`CONTRIBUTING.md`、`docs/{findings,architecture,api_inventory,protocol,protocol_captured,authentication,troubleshooting}.md` |
| 证据 | `frontend/summary.md`、`frontend/endpoints_raw.json`（135 条 `/api` 路径、125 个主机、source map 404） |

## 下一步（当前状态与可继续的方向）

抓包与回填已完成，v1.0.0 可正常对话。若要继续推进：

```bash
# 1) 复核环境与协议指纹
python tools/healthcheck.py --direct
python tools/selfcheck.py            # 会真实调用上游（消耗额度），--offline 可跳过

# 2) 想补齐「文件上传 / 图片输入」时：抓一次带附件的会话
python tools/capture.py --interactive --har --label upload
python tools/diff_captures.py captures/upload/requests.json
#    然后把 genUploadInfo 请求体、登记接口、消息体附件字段回填 profile.yaml

# 3) 上游变更时：重新抓包 → 差分 → 只改 profile → 更新指纹快照
python tools/capture.py --interactive --label after-change
python tools/diff_captures.py captures/after-change/requests.json
python tools/healthcheck.py --direct --write-snapshot
```

优先级建议：

| 优先级 | 事项 | 需要的证据 |
| --- | --- | --- |
| P1 | 文件上传 / 图片输入 | `genUploadInfo` 请求体 + COS 返回 + 消息体附件字段 + 登记接口 |
| P1 | 上游原生 `tool_calls` | 触发一次网页端工具调用的完整 SSE |
| P2 | 消息详情 / 历史补全 | 打开历史会话时的请求与响应 |
| P2 | 联网搜索的显式事件 | 开启联网后的 SSE 帧差异 |
| P3 | 采样参数透传 | 不同 `temperature` 下的请求体差异 |

> 提交证据前请先脱敏（Cookie / token / UID / 手机号）；只提供字段名与结构即可。
> 任何"绕过风控 / 批量 / 账号池"的需求都会被拒绝，见 [DISCLAIMER.md](DISCLAIMER.md)。

