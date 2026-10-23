# API 接口清单（api_inventory.md）

> [!WARNING]
> **本文件是阶段 1（公开 bundle 静态分析）的候选清单，已被真实抓包取代。**
> 下表中的 `inferred` / `unknown` 只代表“当时的推测与未知”，**不是当前实现状态**。
>
> 当前有效结论请看：
> * [`protocol_captured.md`](protocol_captured.md) —— 抓包确认的端点、请求体、SSE 生命周期；
> * [`../protocol/current/profile.yaml`](../protocol/current/profile.yaml) —— 机器可读的协议事实（含 evidence）；
> * `GET /v1/protocol` —— 服务运行时的协议摘要。
>
> 抓包确认的端点：`generate/id`、`chat/{id}`、`chat/repeat/{id}`、`stop/chat/{id}`、
> `chat/conversation/list`、`getChatConfig`、`getuserinfo`。
> 仍未确认：文件上传链路、图片输入、原生 `tool_calls`、消息详情。

## 任务书 §14 要求的表格（历史快照）

| 功能 | Method | URL | 状态 | 证据 |
| ---- | ------ | --- | ---- | ---- |
| 用户信息 | GET | `/api/new-portal/getuserinfo` | inferred | E4（bundle 字符串表） |
| 模型列表 | GET | `/api/models` | inferred | E4；另有 `/api/agent/model/list` 候选 |
| 新建会话 | — | — | unknown | 未确认是否存在独立建会话接口，可能首条消息自动建 |
| 发送消息 | POST | `/api/new-portal/chat` | inferred | E4，**必须抓包时间线确认** |
| 流式响应 | — | — | unknown | E8：全仓无 `text/event-stream`，机制未定 |
| 历史会话 | POST | `/api/user/agent/conversation/v1/detail` | inferred | E4 |
| 消息详情 | — | — | unknown | 未定位 |
| 停止生成 | POST | `/api/new-portal/stop/chat` | inferred | E4 |
| 文件上传 | POST | `/api/new-portal/chat/resource/genUploadInfo` | inferred | E4 + E7（COS 直传） |

## 其他已发现的候选接口（来自公开 bundle，非验证）

| 路径 | 疑似用途 | 状态 |
| --- | --- | --- |
| `/api/new-portal/chat/repeat` | 重新生成 | inferred |
| `/api/new-portal/getChatConfig` | 聊天/模型参数 | inferred |
| `/api/new-portal/chat/conversation/update` | 会话重命名/更新 | inferred |
| `/api/new-portal/chat/conversation/clear` | 清空会话 | inferred |
| `/api/new-portal/chat/conversation/history/del` | 删除历史 | inferred |
| `/api/new-portal/chat/conversation/share` | 分享会话 | inferred |
| `/api/new-portal/user/agent/conversation/continue` | 继续生成 | inferred |
| `/api/user/agent/conversation/updateModel` | 切换模型 | inferred |
| `/api/user/agent/quotainfo` | 额度信息 | inferred |
| `/api/user/agent/conversation/textcheck` | 内容审核 | inferred |
| `/api/resource/fileParse` | 文件解析 | inferred |
| `/api/new-portal/chat/resource/v1/download` | 附件下载（返回 realUrl） | inferred |
| `/api/image/{clarity,edit/asset/save,elimination,outpainting,removewatermark,style,search}` | 图像能力 | inferred |
| `/api/new-portal/audio/synthesis` | TTS | inferred |
| `/api/new-portal/asr/{chatinput/recognize,file/recognize,realtime/recognize}` | 语音识别 | inferred |

## 未发现（到目前为止）

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| `api.hunyuan.tencent.com` | **不存在于网页端** | E3：前端 bundle 中 0 次出现 |
| `/v1/chat/completions` 形态的上游接口 | 不存在 | 网页端用 `/api/new-portal/**` 而非 OpenAI 风格 |
| source map | 不可用 | E10：`.js.map` 全部 404，`sourceMappingURL` 0 次 |
| WebSocket 聊天通道 | unknown | 仅在 vendor/monitor 中出现，无法证明聊天在用 |

## 如何把 inferred 变成 confirmed

```bash
# 1) 抓包（需要你自己完成一次登录与操作）
python tools/capture.py --interactive --label baseline

# 2) 看汇总与差分
type captures\baseline\summary.md
python tools/diff_captures.py captures/baseline/requests.json

# 3) 按证据回填 protocol/current/profile.yaml：
#    - endpoint 的 status: inferred -> confirmed
#    - request_schema.body_template（真实字段）
#    - response_schema.*_paths（真实字段路径）
#    - stream.transport / parser / event_map（真实帧格式）
#    - models / default_model（真实模型 ID）

# 4) 重新渲染本文件
python -c "from analyzer.endpoint_discovery import *; ...（见 tools/selfcheck.py 用法）"
```
