# 安全策略 / Security Policy

## 报告安全问题

请**不要**通过公开 issue 报告安全漏洞或凭证泄露。请使用 GitHub 的
**Private vulnerability reporting**（仓库 → Security → Report a vulnerability），
或在仓库主页找到维护者联系方式后私下告知。

请在报告中包含：受影响版本/提交、复现步骤、影响范围、可能的修复建议。
我们会在 7 天内确认收到，并在修复后与你协商公开时间。

> 特别提醒：**不要在报告里附带真实 Cookie、`storage_state.json`、HAR 文件、
> 账号 UID 或二维码截图**。请用 `mock-value` 之类的占位值复现。

## 支持范围

| 版本 | 安全修复 |
| --- | --- |
| 最新 `main` | ✅ |
| 更早提交 | ❌（请先升级） |

## 本项目的安全模型

* **仅本机使用**：默认监听 `127.0.0.1:1010`。请勿把服务暴露到公网 / 局域网；
  如确需，必须自行加反向代理、TLS、鉴权与访问控制。
* **凭证边界**：登录凭证（Cookie）只保存在本机 `secrets/`；代码不读取账号密码，
  不做自动登录，不上传任何凭证。
* **脱敏**：日志与抓包对 `Cookie` / `Authorization` / `token` / `sessionid` 等
  字段做掩码（见 `app/security/redaction.py`，有单元测试覆盖）。
* **API Key**：`config.yaml: server.api_keys` 是本代理的**本地**鉴权，默认值
  `changeme-please` 仅供示例；请改成随机长字符串。
* **已知风险**：上游接口来自逆向分析，其返回内容按不可信输入处理（解析失败不会
  崩溃，而是回退为纯文本或明确报错）。

## 仓库卫生（发布前自查）

```bash
git status --short          # 不应出现 secrets/ captures/ logs/ .env
git ls-files | findstr /i "storage_state"   # 应为空
```

`.gitignore` 已忽略：`secrets/`、`captures/`、`logs/`、`.env*`、`*.pem`、`*.log`、
浏览器 Profile 目录等。如果你曾把凭证提交进历史，请**立即吊销上游会话并重写历史**，
仅删除文件不足以消除泄露。
