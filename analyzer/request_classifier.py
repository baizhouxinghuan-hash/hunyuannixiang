"""请求分类器（任务书 §4 / §6）。

规则说明
--------
* **不按 URL 名字下结论**：标签由“请求指纹 + 响应特征 + 调用时机”共同决定；
* 每个结果都带 ``confidence`` 与 ``reasons``，便于人工复核；
* 分类只是“候选”，写入 docs/api_inventory.md 时仍必须保留
  confirmed / partially confirmed / inferred / unknown 的状态区分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from analyzer.fingerprint import parse_url

CLASSIFICATION_LABELS = (
    "login",
    "user_info",
    "model_list",
    "chat_config",
    "create_conversation",
    "send_message",
    "stream_response",
    "stop_generation",
    "regenerate",
    "edit_message",
    "delete_conversation",
    "list_conversations",
    "history",
    "message_detail",
    "upload",
    "download",
    "search",
    "telemetry",
    "static_asset",
    "unknown",
)


@dataclass
class Classification:
    label: str = "unknown"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 2),
            "reasons": self.reasons,
            "alternatives": self.alternatives,
        }


#: 关键词只作为**辅证**（权重很低，不足以单独定案）
_HINTS: dict[str, tuple[str, ...]] = {
    "login": ("login", "signin", "passport", "oauth", "tmpToken".lower()),
    "user_info": ("getuserinfo", "userinfo", "user/info", "profile"),
    "model_list": ("models", "model/list", "getmodels"),
    "chat_config": ("getchatconfig", "chat/config"),
    "stop_generation": ("stop", "abort", "cancel"),
    "regenerate": ("repeat", "regenerate", "retry"),
    "history": ("history", "detail", "conversation/list"),
    "upload": ("upload", "genuploadinfo", "fileparse"),
    "download": ("download", "resource/v1/download"),
    "search": ("search", "websearch"),
    "telemetry": ("beacon", "report", "trace", "monitor", "stat"),
}

_CHATISH_BODY_KEYS = {"prompt", "message", "content", "messages", "query", "text", "input", "question"}
_CONVERSATION_KEYS = {"conversation_id", "chat_id", "session_id", "chat_record_id", "chatrecordid"}
_MODEL_KEYS = {"model", "model_id", "modelid", "model_name"}
_STREAM_CT = ("event-stream", "ndjson", "stream")

STATIC_EXT = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2", ".map", ".ico", ".gif", ".webp",
)


def _is_static(host: str, path: str, resource_type: str | None) -> bool:
    if resource_type in ("image", "stylesheet", "font", "script", "media"):
        return True
    return path.lower().endswith(STATIC_EXT)


def classify(record: Mapping[str, Any], *, prev_record: Mapping[str, Any] | None = None) -> Classification:
    """对单条请求记录给出候选标签。"""
    method = str(record.get("method") or "GET").upper()
    host, path, _query = parse_url(str(record.get("url") or ""))
    lowered_path = path.lower()
    body = record.get("request_body_json")
    body_keys = set(body.keys()) if isinstance(body, Mapping) else set()
    resp_ct = str(record.get("response_content_type") or "").lower()
    status = record.get("status")

    if _is_static(host, path, record.get("resource_type")):
        return Classification("static_asset", 0.99, ["静态资源（扩展名/资源类型）"])

    def hinted(label: str) -> bool:
        return any(word in lowered_path for word in _HINTS.get(label, ()))

    # 1) 流式响应 = 最强的“发送消息”信号
    is_stream = any(token in resp_ct for token in _STREAM_CT) or bool(record.get("stream_frames"))
    if is_stream and method == "POST":
        reasons = [f"响应 Content-Type 表示流式：{resp_ct or 'unknown'}"]
        chat_keys = sorted(body_keys & (_CHATISH_BODY_KEYS | _MODEL_KEYS))
        if chat_keys:
            reasons.append(f"请求体含对话/模型字段：{chat_keys}")
            return Classification("send_message", 0.9, reasons, ["stream_response"])
        return Classification("send_message", 0.6, reasons, ["stream_response"])

    # 2) 非流式但请求体像“发消息”
    chat_like = bool(body_keys & _CHATISH_BODY_KEYS)
    has_model = bool(body_keys & _MODEL_KEYS)
    has_conversation = bool(body_keys & _CONVERSATION_KEYS)
    if method == "POST" and chat_like:
        reasons = [f"POST + 请求体含对话字段 {sorted(body_keys & _CHATISH_BODY_KEYS)}"]
        confidence = 0.75 if has_model else 0.6
        if has_model:
            reasons.append("含模型字段")
        if hinted("send_message"):
            confidence += 0.05
        if status == 200:
            reasons.append("HTTP 200")
        alternatives = [] if has_model else ["history"]
        return Classification("send_message", min(confidence, 0.9), reasons, alternatives)

    # 3) 动作型接口
    for label in ("stop_generation", "regenerate", "upload", "download"):
        if method in ("POST", "DELETE") and hinted(label):
            reasons = [f"路径关键词命中 {label}"]
            confidence = 0.6
            if has_conversation:
                reasons.append("含会话字段")
                confidence = 0.7
            return Classification(label, confidence, reasons, ["unknown"])

    # 4) 只读接口
    if method == "GET":
        for label, confidence in (("model_list", 0.7), ("user_info", 0.7), ("history", 0.55), ("chat_config", 0.6)):
            if hinted(label):
                return Classification(label, confidence, [f"GET + 路径关键词命中 {label}"], ["unknown"])

    # 5) 遥测噪声
    if hinted("telemetry") or host.endswith("beacon.qq.com") or body_keys & {"event", "events"}:
        return Classification("telemetry", 0.6, ["路径/体特征符合埋点上报"])

    # 6) 兜底
    if method == "POST" and has_conversation and not chat_like:
        return Classification(
            "history", 0.4, ["POST + 会话字段但无对话字段（更可能是读历史，任务书 §8）"], ["send_message"]
        )
    return Classification("unknown", 0.2, ["无匹配规则"], [])


def classify_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """对一批记录分类（结果里附带 ``classification`` 字段）。"""
    out: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        result = classify(record, prev_record=records[index - 1] if index else None)
        enriched = dict(record)
        enriched["classification"] = result.to_dict()
        out.append(enriched)
    return out

