# ⚠️ 免责声明 / Disclaimer

> **请先读完这一页再使用本项目。**
> **Read this page before using this project.**

## 1. 最重要的一句话

**本项目仅供个人学习、技术研究与协议分析使用，严禁任何商业用途；
本项目与腾讯公司无关，未获其授权、认可或背书。**

> **This project is for personal study, technical research and protocol analysis only.
> Commercial use is strictly prohibited. It is NOT affiliated with, authorized by, or
> endorsed by Tencent.**

## 2. 本项目做了什么（以及没做什么）

| 本项目会做 | 本项目绝不做 |
| --- | --- |
| 用**你自己的浏览器会话**（你本人手动登录后导出的 Cookie）把混元（AI Studio）网页端已具备的对话能力，封装成**本机** OpenAI 兼容接口 | ❌ 不实现自动登录、验证码识别、短信/扫码代过 |
| 把**你主动操作**产生的请求/响应抓下来做协议分析，凭证自动脱敏 | ❌ 不收集、不上传账号密码或 Cookie；凭证只落在本机 `secrets/`（已在 `.gitignore`） |
| 协议未确认处返回明确的 `501 protocol_unknown` | ❌ 不绕过付费、配额、风控、地区限制或任何访问控制 |
| 在 README / 文档里如实标注“已确认 / 未确认 / 推测” | ❌ 不提供、不转售账号，不做“代挂”“代登录”“共享账号” |

本项目**不包含**任何腾讯的源代码、模型权重或私有数据；所有协议事实来自公开前端
bundle 分析 + 用户本人账号下的流量观察。

## 3. 合规红线（请务必遵守）

使用本项目即表示你承诺**不会**将其用于：

1. **任何商业目的**（付费服务、产品集成、企业内部生产系统、SaaS/API 转售、
   收费代部署等）—— 详见 [`LICENSE`](LICENSE)；
2. 违反腾讯混元 / AI Studio《用户协议》《隐私政策》或当地法律法规的行为；
3. 绕过付费、配额、限流、风控、实名或其他访问控制；
4. 批量注册、养号、账号出租出售、共享账号、代过验证；
5. 未授权访问他人账号、会话、文件或隐私数据；
6. 任何危害网络安全、侵犯他人知识产权或其他合法权益的行为。

> 若你不确定某种用法是否合规：**默认它不合规，请不要做。**

## 4. 风险提示（后果自负）

* **账号风险**：上游可能检测异常调用模式（高频、非浏览器指纹、并发、异常 UA），
  可能导致限流、风控、封禁。请保持 `rate_limit.max_concurrency: 1`，不要压测。
* **稳定性风险**：上游接口来自逆向分析，**随时可能变更**（字段改名、加签名、
  换域名、加风控）。本项目会以 `501` / `502` / `504` 明确失败，而不是伪造结果。
* **数据风险**：你的提问会经本机转发到上游，等同于你在网页端自己提问；请勿发送
  你不愿被上游记录的内容（密钥、身份证、医疗记录等）。
* **费用风险**：若你的账号存在计费/额度，调用会**消耗你自己的额度**；本项目不做
  任何额度保护，也不会自动重试（避免重复扣费）。
* **无担保**：软件按“现状”提供，不承诺可用性、正确性、安全性。

## 5. 凭证与隐私

* 登录凭证保存在本机 `secrets/storage_state.json` 与 `secrets/browser_profile/`，
  这两个路径以及 `captures/`、`logs/` 均已在 `.gitignore` 中；
* 抓包与日志会对 `Cookie`、`Authorization`、`token`、`sessionid` 等字段做脱敏；
* **提交 issue / PR 前请自查**：不要贴出完整 Cookie、请求头、HAR、账号 UID、
  手机号、二维码截图。

发现凭证泄露或安全问题，请按 [`SECURITY.md`](SECURITY.md) 私下报告，不要公开贴出。

## 6. 商标与版权

“腾讯”“混元”“Hunyuan”“AI Studio”等名称与商标归其权利人所有；本项目仅作描述性
使用，不构成任何关联、赞助或背书。本项目代码与文档的许可见 [`LICENSE`](LICENSE)
（非商业学习许可，商业使用需事先书面授权）。

## 7. 免责

在适用法律允许的最大范围内，作者与贡献者不对因使用或无法使用本项目而产生的任何
直接或间接损失负责（包括账号封禁、额度消耗、数据丢失、业务中断、第三方索赔）。
**你使用本项目的一切后果由你自行承担。**

---

## English Summary

* **Non-commercial only.** Personal study / research / protocol analysis. Commercial
  use requires prior written permission (see [`LICENSE`](LICENSE)).
* **Not affiliated with Tencent.** No authorization, endorsement or sponsorship.
* **Your account, your responsibility.** The tool reuses a session that *you* created
  by logging in manually; it never performs login, CAPTCHA solving, or credential
  collection.
* **No bypassing.** Do not use it to circumvent payment, quota, rate limits, risk
  controls, or any access control.
* **Fragile by nature.** The upstream protocol was reverse-engineered and may change
  at any time; failures are reported explicitly (`501` / `502` / `504`), never faked.
* **No warranty.** Provided "AS IS"; you bear all risks (account bans, quota usage,
  data loss).
* **Keep secrets secret.** `secrets/`, `captures/` and `logs/` are git-ignored; never
  paste cookies, HAR files or account identifiers into issues.
