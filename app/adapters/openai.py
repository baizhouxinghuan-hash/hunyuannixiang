"""OpenAI 兼容适配层。

职责
----
1. OpenAI messages -> 上游消息结构（**不猜字段名**：由 profile 决定是否需要全量历史）；
2. 上游 :class:`ChatChunk` -> OpenAI 非流式响应 / 流式 chunk；
3. reasoning（思考内容）策略：passthrough / drop / as_text；
4. usage / finish_reason 归一化。

system message 兼容策略（任务书 §18）
------------------------------------
上游是否接受 system 消息尚未确认（profile.features），因此：
* 若 profile 未确认，则把 system 内容折叠进第一条 user 消息之前，
  作为普通文本（**并在响应头与日志中标注 system_folded=true**）；
* 具体策略可由 profile.request_schema.system_mode = "as_message" | "fold_into_user" 指定，
  未确认时默认 fold_into_user（保守、不会因为上游拒绝 system 而 400）。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.protocol.profile import Profile
from app.upstream.errors import ProtocolUnknownError
from app.upstream.streaming import ChatChunk

#: OpenAI finish_reason 合法值
OPENAI_FINISH_REASONS = {"stop", "length", "tool_calls", "content_filter", "function_call"}


@dataclass
class ChatResult:
    """聚合后的完整回复。"""

    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, Any] | None = None
    conversation_id: str | None = None
    message_id: str | None = None


def _content_to_text(content: Any) -> str:
    """把 OpenAI content（字符串或分段数组）里的文本抽出来。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, Mapping):
                if part.get("type") == "text" and part.get("text") is not None:
                    parts.append(str(part["text"]))
        return "".join(parts)
    return str(content)


def content_to_text(content: Any) -> str:
    """公开版本（供 API 层复用），语义同 :func:`_content_to_text`。"""
    return _content_to_text(content)


def _has_non_text_parts(content: Any) -> list[str]:
    """返回 content 中的非文本分段类型（例如 image_url / file）。"""
    kinds: list[str] = []
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        for part in content:
            if isinstance(part, Mapping) and part.get("type") not in (None, "text"):
                kinds.append(str(part["type"]))
    return kinds


def to_upstream_messages(
    messages: Iterable[Mapping[str, Any]],
    profile: Profile,
) -> list[dict[str, Any]]:
    """OpenAI messages -> 上游消息列表（仅在 full_history 模式下使用）。

    * system 消息按 profile 策略处理；
    * 出现图片/文件分段而 profile 未确认支持时 -> ProtocolUnknownError。
    """
    system_mode = str((profile.request_schema or {}).get("system_mode") or "fold_into_user").strip()
    if system_mode not in ("as_message", "fold_into_user"):
        raise ProtocolUnknownError("request_schema.system_mode", f"未知取值 {system_mode!r}")

    image_feature = profile.features.get("image_input")
    out: list[dict[str, Any]] = []
    pending_system: list[str] = []

    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        kinds = _has_non_text_parts(content)
        if kinds and not (image_feature and image_feature.known and image_feature.value):
            raise ProtocolUnknownError(
                "feature:image_input",
                f"请求包含非文本分段 {kinds}，但图片输入能力尚未由抓包确认"
                "（见 docs/api_inventory.md 的 image_input 行）",
            )
        if role == "system":
            if system_mode == "as_message":
                out.append({"role": "system", "content": _content_to_text(content)})
            else:
                pending_system.append(_content_to_text(content))
            continue

        text = _content_to_text(content)
        if pending_system:
            text = "\n\n".join(pending_system) + "\n\n" + text if text else "\n\n".join(pending_system)
            pending_system = []
        out.append({"role": role, "content": text})

    if pending_system:
        # 只有 system 消息的请求：放到一条 user 消息里
        out.append({"role": "user", "content": "\n\n".join(pending_system)})
    return out


def collect_passthrough(request: Mapping[str, Any], profile: Profile) -> dict[str, Any]:
    """按 profile.request_schema.passthrough_params 收集需要透传的参数。

    只有抓包确认过的参数名才会被透传 —— 其余参数静默忽略（不猜测上游字段）。
    """
    names = (profile.request_schema or {}).get("passthrough_params") or []
    if not isinstance(names, Sequence) or isinstance(names, str):
        return {}
    reserved = {"model", "messages", "stream", "stream_options"}
    return {
        str(name): request[str(name)]
        for name in names
        if name in request and str(name) not in reserved
    }


def normalize_finish_reason(value: Any, *, default: str = "stop") -> str:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in OPENAI_FINISH_REASONS:
        return text
    mapping = {
        "": default,
        "none": default,
        "finished": "stop",
        "complete": "stop",
        "completed": "stop",
        "end": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "max_length": "length",
        "length": "length",
        "tool_call": "tool_calls",
        "function_call": "function_call",
        "search": "stop",
    }
    return mapping.get(text, default)


def new_completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def accumulate(chunks: Iterable[ChatChunk]) -> ChatResult:
    """把流式 chunk 聚合为非流式结果。"""
    result = ChatResult()
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    for chunk in chunks:
        if chunk.text:
            text_parts.append(chunk.text)
        if chunk.reasoning:
            reasoning_parts.append(chunk.reasoning)
        if chunk.tool_call:
            result.tool_calls.append(dict(chunk.tool_call))
        if chunk.finish_reason:
            result.finish_reason = normalize_finish_reason(chunk.finish_reason)
        if chunk.usage:
            result.usage = dict(chunk.usage)
        if chunk.conversation_id:
            result.conversation_id = chunk.conversation_id
        if chunk.message_id:
            result.message_id = chunk.message_id
    result.text = "".join(text_parts)
    result.reasoning = "".join(reasoning_parts)
    if result.tool_calls and result.finish_reason == "stop":
        result.finish_reason = "tool_calls"
    return result


def _apply_reasoning(text: str, reasoning: str, mode: str) -> str:
    if mode == "as_text" and reasoning:
        return f"{reasoning}\n\n{text}" if text else reasoning
    return text


def result_to_openai_response(
    result: ChatResult,
    *,
    model: str,
    request_id: str | None = None,
    created: int | None = None,
    reasoning_mode: str = "passthrough",
) -> dict[str, Any]:
    """上游结果 -> OpenAI 非流式响应体。"""
    message: dict[str, Any] = {
        "role": "assistant",
        "content": _apply_reasoning(result.text, result.reasoning, reasoning_mode),
    }
    if reasoning_mode == "passthrough" and result.reasoning:
        message["reasoning_content"] = result.reasoning
    if result.tool_calls:
        message["tool_calls"] = result.tool_calls

    body: dict[str, Any] = {
        "id": request_id or new_completion_id(),
        "object": "chat.completion",
        "created": created if created is not None else int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": normalize_finish_reason(result.finish_reason),
            }
        ],
        # 上游未提供用量时返回 null，不伪造数字
        "usage": _normalize_usage(result.usage) if result.usage else None,
    }
    if result.conversation_id:
        body["conversation_id"] = result.conversation_id
    return body


def _normalize_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    """尽力归一化 usage；缺失字段保留 None，**不编造**。"""
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    if total is None and isinstance(prompt, int) and isinstance(completion, int):
        total = prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "x_raw": dict(usage),
    }


def chunk_to_openai_stream(
    chunk: ChatChunk,
    *,
    model: str,
    request_id: str,
    created: int,
    reasoning_mode: str = "passthrough",
    include_usage: bool = False,
) -> dict[str, Any] | None:
    """上游 chunk -> OpenAI ``chat.completion.chunk``。

    返回 None 表示该 chunk 无需下发（例如只含 usage 且客户端未请求 usage）。
    """
    delta: dict[str, Any] = {}
    if chunk.text:
        delta["content"] = chunk.text
    if chunk.reasoning:
        if reasoning_mode == "passthrough":
            delta["reasoning_content"] = chunk.reasoning
        elif reasoning_mode == "as_text":
            delta["content"] = (delta.get("content") or "") + chunk.reasoning
    if chunk.tool_call:
        delta["tool_calls"] = [_normalize_tool_call(chunk.tool_call)]

    finish = normalize_finish_reason(chunk.finish_reason) if chunk.finish_reason else None

    if not delta and finish is None:
        if chunk.usage and include_usage:
            return _usage_chunk(request_id, model, created, chunk.usage)
        return None

    body: dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if chunk.usage and include_usage:
        body["usage"] = _normalize_usage(chunk.usage)
    return body


def final_stream_chunk(
    *,
    model: str,
    request_id: str,
    created: int,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """收尾 chunk（空 delta + finish_reason）。"""
    return {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": normalize_finish_reason(finish_reason)}
        ],
    }


def first_stream_chunk(*, model: str, request_id: str, created: int, role: str = "assistant") -> dict[str, Any]:
    """首包 chunk（OpenAI 客户端通常期望先收到 role）。"""
    return {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": role}, "finish_reason": None}],
    }


def error_stream_chunk(message: str, *, code: str = "stream_error") -> dict[str, Any]:
    """流中途出错的兼容写法：以标准 error 事件下发。"""
    return {"error": {"message": message, "type": "server_error", "code": code}}


def sse_encode(payload: Any) -> str:
    """把对象编码成一行 SSE data 事件。"""
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _usage_chunk(request_id: str, model: str, created: int, usage: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [],
        "usage": _normalize_usage(usage),
    }


def _normalize_tool_call(tool_call: Mapping[str, Any]) -> dict[str, Any]:
    """上游 tool_call -> OpenAI ``delta.tool_calls`` 元素。

    上游结构未确认时，原始内容放在 ``x_raw`` 中，不猜测 function.name/arguments。
    """
    call_id = str(tool_call.get("id") or f"call_{uuid.uuid4().hex[:12]}")
    index = int(tool_call.get("index", 0))
    function = tool_call.get("function")
    if isinstance(function, Mapping):
        arguments = function.get("arguments")
        return {
            "index": index,
            "id": call_id,
            "type": "function",
            "function": {
                "name": function.get("name"),
                "arguments": arguments
                if isinstance(arguments, str)
                else json.dumps(arguments, ensure_ascii=False),
            },
        }
    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {
            "name": str(tool_call.get("name") or "unknown_tool"),
            "arguments": json.dumps(tool_call, ensure_ascii=False),
        },
        "x_raw": dict(tool_call),
    }


