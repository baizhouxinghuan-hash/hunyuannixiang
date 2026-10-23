# protocol/v1

这里存放**抓包确认后冻结**的协议版本快照。

约定
----
* `protocol/current/profile.yaml` 是运行时使用的档案（会被回填、迭代）；
* 每次完成一次有效抓包并回填后，把该文件复制一份到
  `protocol/v1/profile.<日期>.yaml`（或 `v2/...`）作为历史版本；
* `protocol/current/schema_fingerprint.json` 由程序自动写出（启动时比对），
  指纹不一致会在 `/health` 与启动日志里输出
  `WARNING: upstream protocol changed`（任务书 §37 / §43）。

回填流程
--------
```bash
python tools/capture.py --interactive --label baseline   # 你手动登录并操作
python tools/diff_captures.py captures/requests.json     # 差分：固定/变量/签名字段
# 按 captures/ 与 diff_report.md 里的证据，把 profile.yaml 的 status 从
# inferred/unknown 改为 confirmed，并填入 endpoint/body/流式映射
python tools/healthcheck.py --direct                     # 校验指纹与上游可达性
```
