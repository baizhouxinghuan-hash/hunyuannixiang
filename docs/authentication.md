# 认证（authentication.md）

## 结论速览

| 项目 | 值 | 状态 |
| --- | --- | --- |
| 认证载体 | **Cookie**（`credentials: "include"`） | ✅ confirmed（抓包） |
| 必带 Cookie | `hunyuan_source`、`hunyuan_token`、`hunyuan_user` | ✅ confirmed |
| 是否需要 `Authorization` | **不需要**（HAR 中无该头） | ✅ confirmed |
| 是否存在签名 / nonce / 时间戳校验 | 未发现 | 🟡 partially_confirmed |
| 必带请求头 | `chat_version: v1`、`credentials: include`、`mode: cors`、`X-AgentID: HunyuanDefault`、`X-Requested-With: XMLHttpRequest`、`Origin/Referer: https://aistudio.tencent.com`、`Content-Type: text/plain;charset=UTF-8` | ✅ confirmed |
| 未登录时的表现 | `api.hunyuan.tencent.com` 返回 JSON 错误 / `401`/`403`（**不是** SPA HTML） | ✅ confirmed |
| 凭证来源 | 用户自己浏览器登录后导出的 `storage_state.json` | 本项目设计 |
| 有效期 | cookie `expires` 可读；session cookie 无法预判 | — |

## 本项目如何认证（§11 / §30 / §31）

```text
你手动登录（Playwright 有头浏览器）
        ↓
secrets/storage_state.json  ← tools/capture.py 自动导出
        ↓
本地 API 启动时读取该文件，注入 Cookie 头
        ↓
请求 https://api.hunyuan.tencent.com/api/**（跨域；Origin=aistudio.tencent.com）
```

**不做**：验证码识别、登录绕过、账号密码自动登录、第三方 Cookie 窃取、
伪造他人身份、攻击服务器、破解服务端密钥。

## 哪些值长期有效 / 每次变化（抓包确认）

| 类别 | 说明 | 状态 |
| --- | --- | --- |
| 长期有效 | `hunyuan_source` / `hunyuan_token` / `hunyuan_user` 登录 Cookie（有效期见 `expires`） | ✅ confirmed |
| 每次请求变化 | 未发现 `nonce` / `timestamp` / `signature` 参与鉴权 | 🟡 partially_confirmed |
| 每次会话变化 | `conversation_id`（`generate/id` 返回）、`chatRecordId`（流内） | ✅ confirmed |
| 只是浏览器环境信息 | `User-Agent`、`sec-ch-ua`、`Accept-Language` 等 | 🟡 partially_confirmed（浏览器环境） |

`tools/diff_captures.py` 会自动把变量字段分类为
会话 / 消息 / 签名 / 时间 / 随机（§35），可用于复核上表。

## 会话检查

```bash
python tools/import_session.py                 # 脱敏打印：cookie 名、域、过期情况
python tools/import_session.py --probe         # 顺带探测上游登录态
python tools/capture.py --interactive          # 重新登录并刷新 storage_state
```

`import_session.py` 输出示例（值一律 `****`）：

```json
{
  "source": "D:/.../secrets/storage_state.json",
  "cookie_count": 7,
  "cookie_names": ["****"],
  "domains": ["aistudio.tencent.com"],
  "expired_cookies": [],
  "origins": ["https://aistudio.tencent.com"]
}
```

## 未登录/过期的表现与处理

1. 上游返回 `401/403`（或 JSON 错误体）→ `AIStudioAuthError`（HTTP 401，OpenAI 风格）；
   若误收到 HTML（例如代理/网关错误页），按“非 JSON 响应”处理，**不解析、不伪装成功**；
2. 错误信息里带 `hint`：提示重新跑 `tools/capture.py --interactive`；
3. `/health` 的 `authenticated` 变为 `false`，`status` 变为 `degraded`；
4. **不会**自动重试登录，也不会重放聊天请求（避免重复扣额度）。

## 安全约束（§5 / §28 / §41 H）

* `secrets/` 已加入 `.gitignore`：`storage_state.json`、`cookie_header.txt`、
  `browser_profile/`、`tokens.json` 等；
* 日志写入前经过 `app/security/redaction.py` 双重脱敏
  （`Authorization: ****`、`Cookie: ****`、`token: ****` …）；
* `captures/` 只写脱敏后的数据；有单测断言"抓包产物里不含原始 cookie 值"
  （`tests/test_redaction.py::test_synthetic_capture_artifacts_contain_no_secrets`）。

## 如果发现签名机制（§11）

假如抓包显示请求体/头里有 `signature`、`nonce`、`timestamp`：

1. **先判断它是否必需**：把该字段删掉重放（`tools/replay.py`），
   若上游仍返回正常结果，说明不是强校验；
2. 若必需，在**公开前端 JS** 中定位生成逻辑（搜索 `signature` / `sign(` /
   对应字段名），在 `analyzer/` 里记录算法与输入；
3. 仍然只用**你自己的**会话与凭证，不尝试伪造他人请求；
4. 若签名依赖服务端下发的挑战（challenge）或需要突破访问控制 → 停止该部分，
   在 `docs/findings.md` 记录
   `需要额外权限/无法在正常 Web 会话范围内验证`。
