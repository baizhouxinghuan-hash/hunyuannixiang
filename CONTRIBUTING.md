# 贡献指南 / Contributing

感谢关注。提交前请先读 [`DISCLAIMER.md`](DISCLAIMER.md) 与 [`LICENSE`](LICENSE)：
**本项目不接受任何商业用途相关的需求**（代挂、批量、绕过风控、账号池等），
此类 issue / PR 会被直接关闭。

## 可以贡献什么

* 🐛 **协议变更**：上游接口改名/加字段，导致 501/502/504 —— 请附**脱敏后**的最小
  证据（字段名、结构，不含 Cookie / token / UID）。
* 🧪 **测试**：补充离线用例（任何用例都必须零网络、可重复）。
* 📝 **文档**：错别字、失效链接、更清楚的排障步骤。
* 🧰 **工具链**：抓包分析脚本的健壮性、可读性改进。
* 🔐 **安全**：见 [`SECURITY.md`](SECURITY.md)，请私下报告。

## 开发环境

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
python -m playwright install chromium   # 只有抓包才需要
```

要求 Python ≥ 3.11（本项目在 3.13 上验证），遵循现有代码风格：
`from __future__ import annotations`、类型标注、中文注释/日志、无第三方风格化依赖。

## 提交前自检

```bash
python -m pytest -q                 # 全绿（默认跳过需要真实会话/网络的用例）
python -m compileall -q app analyzer tools tests
python tools/selfcheck.py --offline # 环境与配置自检
```

## 代码约定

1. **不伪造协议**：未确认的能力一律返回 `501 protocol_unknown`，不要“猜一个实现”。
2. **协议事实只写在 `protocol/current/profile.yaml`**，代码里不要硬编码 URL / 字段名。
3. **错误必须可读**：OpenAI 风格错误体（`error.type/code/message`），中文说明 + ASCII code。
4. **凭证零落盘**：新增日志/抓包点必须走 `app/security/redaction.py` 的掩码。
5. **不自动重试**：上游失败不重发（避免重复生成/扣额度），由客户端决定。
6. **测试零网络**：用 `tests/conftest.py` 的 mock transport；真实上游用例放
   `tests/test_live_upstream.py` 并默认 skip。

## 分支与提交

* 分支：`feat/<topic>`、`fix/<topic>`、`docs/<topic>`。
* 提交信息用祈使句，示例：
  * `fix(chat): release concurrency slot on queue timeout`
  * `docs(readme): correct nonstream_timeout to 90s`
* PR 请说明：**动机 / 改动 / 验证方式**，并确认 `pytest -q` 全绿。
* 涉及协议结论的 PR，请附 `docs/protocol_captured.md` 的更新（证据等级：
  `confirmed` / `partially_confirmed` / `unknown`）。

## 不要提交

`secrets/`、`captures/`、`logs/`、`.env*`、`*.pem`、浏览器 Profile、任何真实
Cookie / HAR / 账号截图。`.gitignore` 已覆盖这些路径，但请提交前用
`git status --short` 再确认一次。
