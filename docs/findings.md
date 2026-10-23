# 侦察发现（findings.md）

> [!IMPORTANT]
> **本文是阶段 1（公开前端 bundle 静态分析）的历史记录，保留原始证据以便复核。**
> 后续真实抓包（`captures/baseline`，见 [`protocol_captured.md`](protocol_captured.md)）
> **推翻了其中两条结论**：
>
> * **E3 作废**：运行时确实是跨域调用 `https://api.hunyuan.tencent.com/api/**`
>   （`Origin: https://aistudio.tencent.com`）；bundle 里没有该字面量，是因为前端用
>   相对路径 `/api/...` + 运行时 baseURL 拼接 —— “同源”属于**错误的静态推断**。
> * **E8 作废**：上游聊天**确认是 SSE**（`Content-Type: text/event-stream`，
>   `type=thought/text/meta`，以 `[DONE]` 结束）。
> * **E9 降级为 partially_confirmed**：未登录时**`aistudio.tencent.com` 的页面路由**
>   会返回 SPA HTML；但 `api.hunyuan.tencent.com` 的真实接口在未登录时返回的是
>   JSON 错误 / 401，因此“JSON 接口收到 HTML = 未登录”只适用于同源路径的旧场景。
>
> **当前有效结论以 [`protocol/current/profile.yaml`](../protocol/current/profile.yaml)
> 为准**；本文只作为“静态分析能到哪一步、为什么会猜错”的方法论留档。

> 本文只记录**有证据可复现**的事实。状态严格区分 `confirmed` /
> `partially confirmed` / `inferred` / `unknown`。
> 复现命令均只访问**公开静态资源**，不使用任何凭证。

复现全部证据：

```bash
python tools/analyze_bundle.py          # 产出 frontend/summary.md 与 frontend/endpoints_raw.json
```

---

## E1 首页是 SPA 外壳 —— confirmed

```bash
curl -s https://aistudio.tencent.com/ | head -c 400
```

* `HTTP 200`，`Content-Type: text/html`
* 体积约 **3.2 KB**，`<title>Hy AI Studio</title>`，`<body><div id="app"></div></body>`
* 所有业务逻辑都在 JS bundle 里，页面本身没有可读的接口信息

## E2 前端资源全部在 CDN —— confirmed

入口 HTML 引用 10 个静态资源（`<script src>` / `<link href>`），全部位于
`https://cdn-portal.hunyuan.tencent.com/public/`：

| 资源 | 大小（字节） |
| --- | --- |
| `runtime__af3a211516436687e0d1.js` | 10,153 |
| `react__c5a2d4fdc3acccbe93d0.js` | 240,002 |
| `monitor__35b65e6889c75bdde006.js` | 201,665 |
| `utils__731e71f93fbdc6dcbbb9.js` | 353,433 |
| `tdesign-icons__00b8e35db39fda858289.js` | 1,512,797 |
| `tdesign__9dc349d2a51371011bc6.js` | 870,896 |
| `cos-sdk__dcbf0ed4dec6fec14296.js` | 321,957 |
| `hy-web__e62837fa9ff77f96314a.js` | 7,617,749 |
| `6473__39850b1ae564af0fd214.js` | 8,024,001 |
| `index__70332c7932348066f545.js` | 3,861,196 |

（文件名为构建哈希，可能随发版变化；`tools/analyze_bundle.py` 会重新解析。）

## E3 网页端 API 与页面**同源** —— ❌ 已被抓包推翻（原判 confirmed）

* `index` bundle 内提取到 **126** 条 `/api/...` 路径；`hy-web` 内 **8** 条；合计 **135** 条去重路径。
* 全部 bundle 中**没有出现** `api.hunyuan.tencent.com`：
  `frontend/summary.md` 中 `是否出现 api.hunyuan.tencent.com：False`。

**当时的结论（错误）**：网页端会话使用的是 `https://aistudio.tencent.com/api/**`。

**抓包事实（`captures/baseline`）**：运行时是
`https://api.hunyuan.tencent.com/api/**`，属于**跨域**请求，
`Origin` / `Referer` 为 `https://aistudio.tencent.com`。
原因：bundle 里写的是相对路径 `/api/...`，真正的 baseURL 由运行时配置拼接，
静态字符串搜索看不到；且打包产物中字面量被拆分/混淆。

**教训**：静态 bundle 只能产出候选，**URL 归属必须以抓包为准**。

> 保留上面两条原始证据，是为了说明“为什么当时会判断同源”，不构成当前结论。

## E4 候选 endpoint 清单 —— inferred（需抓包确认）

来源：bundle 内字符串表 / endpoint 常量映射，例如：

```js
[i.AI_ROLE_CLONE_CONFIG]: "/api/new-portal/chat/conversation/update"
(i)=>{ e.COMMON="/api/vision_platform/resource/genUploadInfo"
       e.CHAT="/api/new-portal/chat/resource/genUploadInfo" ... }
```

与聊天相关的高价值候选：

| 路径 | 疑似功能 |
| --- | --- |
| `/api/new-portal/chat` | **发送消息（主候选）** |
| `/api/new-portal/stop/chat` | 停止生成 |
| `/api/new-portal/chat/repeat` | 重新生成 |
| `/api/new-portal/getChatConfig` | 会话/模型参数配置 |
| `/api/models`、`/api/agent/model/list` | 模型列表 |
| `/api/new-portal/getuserinfo`、`/api/getuserinfo` | 用户信息 |
| `/api/new-portal/chat/conversation/{update,clear,share,history/del}` | 会话管理 |
| `/api/new-portal/user/agent/conversation/v1/detail` | 历史会话详情 |
| `/api/new-portal/chat/resource/genUploadInfo` | 上传（取临时密钥） |
| `/api/resource/fileParse` | 文件解析 |

**注意**：以上只是候选。哪些是"真正让模型开始生成"的接口，必须由抓包时间线证明
（见 `docs/protocol.md` 的判定方法）。

## E5 统一请求封装形态 —— confirmed

bundle 内可见：

```js
(0,_.W)({url:"/api/new-portal/chat/conversation/share", method:"POST", data:{chatRecordIds:[e]}})
```

即：一个内部 wrapper，参数为 `{url, method, data}`，`data` 为 JSON body。

## E6 认证是 Cookie —— partially confirmed

* bundle 内存在 `fetch(url,{credentials:"include"})` → 依赖同站 Cookie。
* 未发现 `Authorization: Bearer`（`Authorization` 字样只出现在 COS SDK 的签名逻辑里）。
* 未发现 `x-requested-with` / `csrf` 字样。
* `hy-web` 中出现 `signature`×6、`nonce`×19、`timestamp`×6；
  `index` 中出现 `signature`×19、`nonce`×7、`timestamp`×20。
  **这些字样是否参与鉴权，尚未确认 → unknown**。

## E7 上传走腾讯 COS 直传 —— inferred

* `/api/new-portal/chat/resource/genUploadInfo` 返回临时密钥（`TmpSecretId` 等），
* 由打包进来的 `cos-sdk` 直传 COS 桶（如 `hunyuan-*-1258344703.cos.ap-guangzhou.myqcloud.com`）。

上传完成后的"登记接口"、消息体里引用附件的字段名，均 **unknown**。

## E8 流式传输机制 —— ❌ 已被抓包推翻（原判 unknown）

关键字计数（`frontend/summary.md` 可复现）：

| bundle | `text/event-stream` | `EventSource` | `WebSocket` | `getReader` | `ReadableStream` | `ndjson` |
| --- | --- | --- | --- | --- | --- | --- |
| index | 0 | ? | 7 | 7 | 10 | 0 |
| hy-web | 0 | 0 | 0 | 0 | 0 | 0 |
| 6473（vendor） | 0 | 25 | 54 | 2 | 6 | 0 |
| utils | 0 | 0 | 0 | 2 | 16 | 0 |

* 全仓**没有** `text/event-stream` 字面量 → 当时无法据此判断上游用 SSE。
* `getReader` / `ReadableStream` 出现在 `index` 与 `utils`，但无法证明聊天链路在用。
* `EventSource` / `WebSocket` 只在 vendor chunk 与 monitor 里出现（多为第三方库）。

**当时的结论**：必须抓包定性，`profile.stream` 保持 `unknown`。

**抓包事实（`session.har`）**：聊天响应为 `Content-Type: text/event-stream`，
事件序列 `speech_type(thought) → traceId → thought × N → thought(已深度思考) →
speech_type(text) → text × N → meta → [DONE]`。当前 profile：
`stream.transport: sse`、`stream.type_field: $.type`、`stream.type_map: {text→text, thought→reasoning, meta→meta}`。

**教训**：前端不用 `EventSource`，而是 `fetch` + `ReadableStream` 手动解析 SSE；
因此“没有 `EventSource`”不等于“不是 SSE”。

## E9 未登录时页面路由返回 SPA HTML —— 🟡 partially_confirmed

```bash
curl -s -o - -w "\n%{http_code} %{content_type}\n" https://aistudio.tencent.com/api/models
# => 200 text/html，内容是 E1 的 SPA 外壳
```

**当时的结论（范围过宽）**：任何 JSON 接口收到 HTML 都判定为未登录。

**抓包修正**：这条只对 **`aistudio.tencent.com` 的页面/同源路由**成立。
真实接口域名 `api.hunyuan.tencent.com` 在未登录/会话过期时返回的是
JSON 错误或 `401/403`，**不是** SPA HTML。因此：

* `app/upstream/errors.py: is_html_response` 仍然保留（防止把 HTML 当 JSON 解析），
  但**不能**再把“收到 HTML”一律判为未登录；
* 认证失败的主判据是 HTTP 状态码 + 响应体 `code/message`。

## E10 没有公开 source map —— confirmed

* `https://cdn-portal.hunyuan.tencent.com/public/<bundle>.js.map` → **404**
* bundle 内 `sourceMappingURL` 出现 **0** 次

**影响**：无法从源码级别还原接口封装（任务书 §13 的结论是"不可用"），
只能依赖 bundle 字符串 + 抓包。

---

## 环境事实（本机实测）

| 项目 | 值 |
| --- | --- |
| Python | 3.13.7 (MSC v.1944, win32) |
| 关键依赖 | playwright 1.58.0、httpx 0.28.1、fastapi 0.115.6、uvicorn 0.34.0、curl_cffi 0.14.0、PyYAML 6.0.3、pydantic 2.12.4 |
| Playwright 浏览器 | `%LOCALAPPDATA%\ms-playwright` 下已有 chromium-1208 等 |
| 网络 | `aistudio.tencent.com`、`api.hunyuan.tencent.com` 与 `cdn-portal.hunyuan.tencent.com` 均可直连 |
| Git | 已 `git init` 并发布为公开仓库；`secrets/`、`captures/`、`logs/` 由 `.gitignore` 排除 |

## Windows 控制台提示

PowerShell 默认代码页会让 Python 输出的中文在**管道/重定向**时变乱码
（文件本身是 UTF-8，内容正确）。建议：

```powershell
chcp 65001
$env:PYTHONIOENCODING='utf-8'
```
