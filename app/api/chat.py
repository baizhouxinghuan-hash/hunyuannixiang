"""``POST /v1/chat/completions``（任务书 §16 / §17 / §19 / §25 / §26）。

关键行为
--------
* 协议未确认时返回**明确的** OpenAI 风格错误（HTTP 501 protocol_unknown），
  绝不返回伪造的“成功”回答；
* 流式响应为 ``text/event-stream`` + ``data: [DONE]``；
* 上游中断/超时会在流内下发 ``error`` 事件并正常结束，且**不自动重发**，
  避免重复生成/重复扣额度（任务书 §26）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from app.adapters import openai as adapter
from app.adapters import tool_bridge
from app.api.deps import verify_api_key
from app.logging_setup import get_request_logger, log_json
from app.upstream.errors import (
    AIStudioError,
    AIStudioRateLimitError,
    AIStudioRequestError,
    AIStudioStreamError,
    AIStudioTimeoutError,
    to_openai_error,
)

router = APIRouter(tags=["chat"])
req_log = get_request_logger()

SSE_MEDIA_TYPE = "text/event-stream"


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI 请求体子集；未识别的字段通过 extra 保留以便给出警告。"""

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    user: str | None = None
    n: int | None = None
    #: 抓包确认可透传的采样参数（其余仅记录警告）
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: Any | None = None
    seed: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    response_format: dict[str, Any] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    #: OpenAI 的思考强度；会被映射到上游 effort（见 _EFFORT_MAP）
    reasoning_effort: str | None = None


def _append_to_last_user(messages: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """把附加说明追加到最后一条 user 消息（供 full_history 的 messages_text 使用）。"""
    out = list(messages)
    for index in range(len(out) - 1, -1, -1):
        if out[index].get("role") != "user":
            continue
        content = out[index].get("content")
        if isinstance(content, list):
            out[index] = {**out[index], "content": [*content, {"type": "text", "text": text}]}
        else:
            base = content if isinstance(content, str) else ""
            out[index] = {**out[index], "content": f"{base}\n\n{text}" if base else text}
        break
    return out


def _message_stats(messages: list[dict[str, Any]], prompt_text: str, tools: Any) -> dict[str, Any]:
    """统计本次请求的 messages 构成，便于排查"人格/历史有没有进 prompt"。

    * ``system_messages`` / ``system_chars``：有几条 system（人格）及其字符数；
    * ``history_messages`` / ``history_chars``：除 system 与**最后一条 user** 之外的条数/字符数
      （即客户端真正带来的历史，含 ``assistant`` / ``tool`` 结果）；
    * ``prompt_chars``：最终拼给上游的 prompt 长度。
    """
    user_indexes = [index for index, item in enumerate(messages) if item.get("role") == "user"]
    last_user = user_indexes[-1] if user_indexes else -1
    system_messages = 0
    system_chars = 0
    history_messages = 0
    history_chars = 0
    for index, message in enumerate(messages):
        role = message.get("role")
        text = adapter.content_to_text(message.get("content")) or ""
        if role == "system":
            system_messages += 1
            system_chars += len(text)
        elif index != last_user:
            history_messages += 1
            history_chars += len(text)
    return {
        "messages_count": len(messages),
        "system_messages": system_messages,
        "system_chars": system_chars,
        "history_messages": history_messages,
        "history_chars": history_chars,
        "tools_count": len(tools or []),
        "prompt_chars": len(prompt_text),
    }


def _last_user_prompt(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            text = adapter.content_to_text(message.content)
            if not text.strip():
                raise AIStudioRequestError("最后一条 user 消息内容为空", param="messages")
            return text
    raise AIStudioRequestError("messages 中必须包含至少一条 user 消息", param="messages")


def _unsupported_warnings(
    body: ChatCompletionRequest, profile, *, tools_bridged: bool = False
) -> list[dict[str, str]]:
    """记录“客户端传了但当前无法透传”的参数。

    返回 ``[{"code": ASCII 码, "message": 中文说明}]``：
    * ``code`` 用于 HTTP 头（必须是 ASCII）；
    * ``message`` 用于日志与 /v1/protocol 排障。
    """
    warnings: list[dict[str, str]] = []
    if body.tools or body.tool_choice:
        if tools_bridged:
            warnings.append(
                {
                    "code": "tools_bridged",
                    "message": "tools 由本地桥接处理（渲染进 prompt，再把模型输出解析为标准 tool_calls）",
                }
            )
        else:
            warnings.append(
                {
                    "code": "tools_not_passed",
                    "message": "tools/tool_choice 暂未透传（上游工具调用协议未确认）",
                }
            )
    if body.response_format:
        warnings.append(
            {
                "code": "response_format_not_passed",
                "message": "response_format 暂未透传（上游是否支持未确认）",
            }
        )
    params = {
        "temperature": body.temperature,
        "top_p": body.top_p,
        "max_tokens": body.max_tokens,
        "stop": body.stop,
        "seed": body.seed,
        "presence_penalty": body.presence_penalty,
        "frequency_penalty": body.frequency_penalty,
    }
    passthrough = set((profile.request_schema or {}).get("passthrough_params") or [])
    ignored = sorted(name for name, value in params.items() if value is not None and name not in passthrough)
    if ignored:
        warnings.append(
            {
                "code": "params_ignored:" + ",".join(ignored),
                "message": f"采样参数未确认可透传，已忽略：{','.join(ignored)}",
            }
        )
    return warnings


def _warning_header(warnings: list[dict[str, str]]) -> str:
    """构造 ASCII 安全的告警头。"""
    codes: list[str] = []
    for item in warnings:
        code = str(item.get("code") or "")
        codes.extend(part for part in code.split(",") if part)
    return ",".join(codes)


def _usage_filter(include_usage: bool, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None or include_usage:
        return payload
    if "usage" in payload:
        payload = {k: v for k, v in payload.items() if k != "usage"}
    return payload


#: OpenAI reasoning_effort -> 上游 effort（取值域见 profile.request_schema.effort_options）
_EFFORT_MAP = {
    "none": "no_think",
    "minimal": "no_think",
    "low": "no_think",
    "medium": "no_think",
    "no_think": "no_think",
    "high": "high",
}


def _resolve_effort(requested: str | None, default: str) -> str:
    """把客户端传来的 reasoning_effort 映射为上游 effort；未传/不认识则用配置默认值。"""
    if not requested:
        return default
    return _EFFORT_MAP.get(str(requested).strip().lower(), default)


# ---------------------------------------------------------------------------
# 流式实现
# ---------------------------------------------------------------------------
async def _sse_stream(
    *,
    request_id: str,
    model: str,
    created: int,
    chunk_iter: AsyncIterator[Any],
    reasoning_mode: str,
    include_usage: bool,
    runtime: Any,
    store_key: str,
    started: float,
    meta: dict[str, Any],
    slot: Any = None,
    effort: str = "",
    tools_requested: bool = False,
    tool_bridge_enabled: bool = False,
    tools_payload: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """把上游 chunk 流转换为 OpenAI SSE 文本流。

    并发槽位由调用方（endpoint）在返回响应前获取，这里只负责归还：
    ``finally`` + ``BackgroundTask`` 双保险，客户端中途断开也不泄漏。
    """
    from app.adapters.openai import (
        chunk_to_openai_stream,
        error_stream_chunk,
        final_stream_chunk,
        first_stream_chunk,
        sse_encode,
    )
    from app.upstream.streaming import ChatChunk

    fallback_text = reasoning_mode == "fallback_text"
    text_len = 0
    reasoning_len = 0
    first_token_ms: float | None = None
    finish_reason = "stop"
    upstream_conv: str | None = None
    reasoning_parts: list[str] = []
    bridge_parts: list[str] = []
    bridged_calls = 0
    text_seen = False

    yield sse_encode(first_stream_chunk(model=model, request_id=request_id, created=created))

    try:
        async for chunk in chunk_iter:
            if chunk.conversation_id:
                upstream_conv = chunk.conversation_id
            if chunk.reasoning:
                reasoning_len += len(chunk.reasoning)
                if fallback_text:
                    reasoning_parts.append(chunk.reasoning)
            if tool_bridge_enabled:
                # 桥接模式：正文先缓冲（模型必须先输出完整 JSON 才能解析），过程中只透传思考/用量
                if chunk.text:
                    bridge_parts.append(chunk.text)
                payload = (
                    chunk_to_openai_stream(
                        ChatChunk(text="", reasoning=chunk.reasoning, usage=chunk.usage),
                        model=model,
                        request_id=request_id,
                        created=created,
                        reasoning_mode=reasoning_mode,
                        include_usage=include_usage,
                    )
                    if (chunk.reasoning or chunk.usage)
                    else None
                )
            else:
                payload = chunk_to_openai_stream(
                    chunk,
                    model=model,
                    request_id=request_id,
                    created=created,
                    reasoning_mode=reasoning_mode,
                    include_usage=include_usage,
                )
            if payload is None:
                continue
            if first_token_ms is None and (chunk.text or chunk.reasoning):
                first_token_ms = round((time.perf_counter() - started) * 1000, 1)
            if chunk.text:
                text_len += len(chunk.text)
                text_seen = True
            if chunk.finish_reason:
                finish_reason = str(chunk.finish_reason)
            yield sse_encode(_usage_filter(include_usage, payload))
    except AIStudioError as exc:
        _, body = to_openai_error(exc)
        yield sse_encode(error_stream_chunk(body["error"]["message"], code=body["error"]["code"]))
        finish_reason = "error"
    except Exception as exc:  # noqa: BLE001 - 流内异常必须可见，不能静默
        yield sse_encode(
            error_stream_chunk(f"internal error: {type(exc).__name__}: {exc}", code="internal_error")
        )
        finish_reason = "error"
    finally:
        try:
            if finish_reason != "error":
                if tool_bridge_enabled:
                    buffered = "".join(bridge_parts)
                    allowed = [spec["name"] for spec in tool_bridge.function_specs(tools_payload)]
                    calls, remaining = tool_bridge.parse_tool_calls(buffered, allowed_names=allowed)
                    if calls:
                        bridged_calls = len(calls)
                        for call in calls:
                            call_payload = chunk_to_openai_stream(
                                ChatChunk(tool_call=call),
                                model=model,
                                request_id=request_id,
                                created=created,
                                reasoning_mode="drop",
                                include_usage=False,
                            )
                            if call_payload is not None:
                                yield sse_encode(call_payload)
                        finish_reason = "tool_calls"
                        text_len = len(remaining)
                    elif buffered:
                        text_payload = chunk_to_openai_stream(
                            ChatChunk(text=buffered),
                            model=model,
                            request_id=request_id,
                            created=created,
                            reasoning_mode="drop",
                            include_usage=False,
                        )
                        if text_payload is not None:
                            yield sse_encode(text_payload)
                        text_len = len(buffered)
                elif fallback_text and not text_seen and reasoning_parts:
                    # 本地兜底策略：上游只回思考、正文为空 -> 把思考当正文补发一次
                    fallback = "".join(reasoning_parts).strip()
                    text_len += len(fallback)
                    fallback_payload = chunk_to_openai_stream(
                        ChatChunk(text=fallback),
                        model=model,
                        request_id=request_id,
                        created=created,
                        reasoning_mode="drop",
                        include_usage=False,
                    )
                    if fallback_payload is not None:
                        yield sse_encode(fallback_payload)
                yield sse_encode(
                    final_stream_chunk(
                        model=model, request_id=request_id, created=created, finish_reason=finish_reason
                    )
                )
            yield "data: [DONE]\n\n"
            if upstream_conv:
                runtime.client.remember_conversation(store_key, upstream_conv)
            log_json(
                req_log,
                20,
                {
                    "event": "chat_stream_end",
                    "request_id": request_id,
                    "model": model,
                    "conversation_key": store_key,
                    "upstream_conversation_id": upstream_conv,
                    "chars": text_len,
                    "reasoning_chars": reasoning_len,
                    "first_token_ms": first_token_ms,
                    "total_ms": round((time.perf_counter() - started) * 1000, 1),
                    "queued_ms": getattr(slot, "waited_ms", 0.0),
                    "effort": effort,
                    "tools_requested": tools_requested,
                    "tools_bridged": tool_bridge_enabled,
                    "bridged_calls": bridged_calls,
                    **(stats or {}),
                    "fallback_text_used": bool(fallback_text and not text_seen and reasoning_parts),
                    "finish_reason": finish_reason,
                    "warnings": [item.get("message") for item in meta.get("warnings", [])],
                },
            )
        finally:
            release = getattr(slot, "release", None)
            if callable(release):
                release()


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    stream_override: bool | None = Query(default=None, include_in_schema=False),
) -> Any:
    runtime = request.app.state.runtime
    cfg = runtime.config
    verify_api_key(request)

    if body.n not in (None, 1):
        raise AIStudioRequestError("仅支持 n=1", param="n")

    messages = [m.model_dump() for m in body.messages]
    prompt = _last_user_prompt(body.messages)
    model_name = body.model or runtime.client.default_model()
    if not model_name:
        raise AIStudioRequestError(
            "未指定 model，且尚未确认默认模型；请先抓包并配置 profile / config.model.default",
            param="model",
        )

    # 工具桥接（本地策略）：把 tools 渲染进 prompt；响应侧再把模型输出解析成标准 tool_calls
    tools_bridged = False
    if (
        cfg.tools.mode == "bridge"
        and body.tools
        and not tool_bridge.tool_choice_disables_tools(body.tool_choice)
    ):
        instructions = tool_bridge.render_tools_instructions(body.tools, body.tool_choice)
        if instructions:
            tools_bridged = True
            prompt = f"{prompt}\n\n{instructions}"
            messages = _append_to_last_user(messages, instructions)

    warnings = _unsupported_warnings(body, runtime.profile, tools_bridged=tools_bridged)
    store_key, upstream_conv = await runtime.client.resolve_conversation(
        explicit=request.headers.get("x-aistudio-conversation-id"),
        session_id=request.headers.get("x-session-id"),
        user=body.user,
        messages=messages,
    )
    extra = adapter.collect_passthrough(body.model_dump(), runtime.profile)
    request_id = adapter.new_completion_id()
    created = int(time.time())
    effort = _resolve_effort(body.reasoning_effort, cfg.model.effort)
    tools_requested = bool(body.tools or body.tool_choice)
    want_stream = bool(body.stream if stream_override is None else stream_override)
    if want_stream and not cfg.stream.enabled:
        want_stream = False
    include_usage = bool((body.stream_options or {}).get("include_usage", False))

    stats = _message_stats(messages, prompt, body.tools)
    headers: dict[str, str] = {"x-request-id": request_id}
    headers["x-aistudio-messages"] = (
        f"{stats['messages_count']}/{stats['system_messages']}/{stats['history_messages']}"
    )
    if warnings:
        headers["x-aistudio-warning"] = _warning_header(warnings)

    if want_stream:
        # 先排队拿槽位（超时 -> 429），再建会话：排队期间不会在上游留下空会话
        slot = _slot(
            runtime,
            timeout=cfg.rate_limit.max_wait_seconds,
            max_concurrency=cfg.rate_limit.max_concurrency,
        )
        try:
            await slot.__aenter__()
            # 抓包确认：发送消息的 path 带 {conversation_id}；没有会话时自动创建
            upstream_conv = await runtime.client.ensure_conversation(upstream_conv, model_name)
            if upstream_conv:
                runtime.client.remember_conversation(store_key, upstream_conv)
            # 同步构造流：协议未确认时在此处直接抛错 -> 返回 501，而不是先 200 再断流
            chunk_iter = runtime.client.stream_message(
                upstream_conv,
                prompt,
                model=model_name,
                messages=messages,
                extra=extra,
                effort=effort,
            )
        except BaseException:
            await slot.__aexit__(None, None, None)
            raise
        started = time.perf_counter()
        return StreamingResponse(
            _sse_stream(
                request_id=request_id,
                model=model_name,
                created=created,
                chunk_iter=chunk_iter,
                reasoning_mode=cfg.reasoning.mode,
                include_usage=include_usage,
                runtime=runtime,
                store_key=store_key,
                started=started,
                meta={"warnings": warnings},
                slot=slot,
                effort=effort,
                tools_requested=tools_requested,
                tool_bridge_enabled=tools_bridged,
                tools_payload=body.tools if tools_bridged else None,
                stats=stats,
            ),
            media_type=SSE_MEDIA_TYPE,
            headers=headers,
            background=BackgroundTask(_release_slot, slot),
        )

    # 非流式：上游只有 SSE，这里等整段生成完再回一个 JSON（任务书 §26）
    started = time.perf_counter()
    slot = _slot(
        runtime,
        timeout=cfg.rate_limit.max_wait_seconds,
        max_concurrency=cfg.rate_limit.max_concurrency,
    )
    async with slot:
        # 拿到槽位后再建会话：排队期间不会在上游留下空会话
        upstream_conv = await runtime.client.ensure_conversation(upstream_conv, model_name)
        if upstream_conv:
            runtime.client.remember_conversation(store_key, upstream_conv)
        try:
            chunk = await asyncio.wait_for(
                runtime.client.send_message(
                    upstream_conv,
                    prompt,
                    model=model_name,
                    messages=messages,
                    extra=extra,
                    effort=effort,
                ),
                timeout=cfg.upstream.nonstream_timeout or None,
            )
        except asyncio.TimeoutError as exc:
            raise AIStudioTimeoutError(
                f"上游生成超过 {cfg.upstream.nonstream_timeout:g}s 仍未完成"
                "（非流式请求必须等上游整段生成完）",
                timeout=cfg.upstream.nonstream_timeout,
                param="stream",
            ) from exc
    result = adapter.accumulate([chunk])
    if result.conversation_id:
        runtime.client.remember_conversation(store_key, result.conversation_id)
    reasoning_chars = len(result.reasoning or "")
    fallback_used = False
    bridged_calls = 0
    if tools_bridged and not result.tool_calls:
        allowed = [spec["name"] for spec in tool_bridge.function_specs(body.tools)]
        calls, remaining = tool_bridge.parse_tool_calls(result.text or "", allowed_names=allowed)
        if calls:
            result.tool_calls = calls
            result.finish_reason = "tool_calls"
            result.text = remaining
            bridged_calls = len(calls)
    if (
        not tools_bridged
        and cfg.reasoning.mode == "fallback_text"
        and not (result.text or "").strip()
        and (result.reasoning or "").strip()
    ):
        # 本地兜底策略：上游只回思考、正文为空 -> 用思考兜底，避免客户端看到空白
        result.text = result.reasoning
        result.reasoning = ""
        fallback_used = True
    payload = adapter.result_to_openai_response(
        result,
        model=model_name,
        request_id=request_id,
        created=created,
        reasoning_mode=cfg.reasoning.mode,
    )
    log_json(
        req_log,
        20,
        {
            "event": "chat_completion",
            "request_id": request_id,
            "model": model_name,
            "conversation_key": store_key,
            "upstream_conversation_id": result.conversation_id,
            "chars": len(result.text),
            "reasoning_chars": reasoning_chars,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "queued_ms": getattr(slot, "waited_ms", 0.0),
            "effort": effort,
            "tools_requested": tools_requested,
            "tools_bridged": tools_bridged,
            "tool_calls_returned": len(result.tool_calls),
            "bridged_calls": bridged_calls,
            **(stats or {}),
            "fallback_text_used": fallback_used,
            "finish_reason": result.finish_reason,
            "warnings": [item.get("message") for item in warnings],
        },
    )
    return JSONResponse(content=payload, headers=headers)


def _slot(runtime: Any, *, timeout: float = 0.0, max_concurrency: int = 1) -> Any:
    """并发限流（任务书 §27）。

    ``timeout > 0`` 时排队超时抛 ``AIStudioRateLimitError``（HTTP 429 + Retry-After），
    而不是让客户端无限等待 —— 上游网页端单并发，长任务排队会非常夸张。
    """
    if runtime.semaphore is None:
        return _NullContext()
    return _SlotAcquireContext(runtime.semaphore, timeout, max_concurrency=max_concurrency)


class _NullContext:
    waited_ms: float = 0.0

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _SlotAcquireContext:
    """可带等待上限的并发槽位。

    ``release()`` 幂等：流式路径由生成器 ``finally`` 与 ``BackgroundTask`` 双重兜底，
    保证客户端中途断开时不会泄漏槽位。
    """

    def __init__(self, semaphore: Any, timeout: float, *, max_concurrency: int = 1) -> None:
        self._semaphore = semaphore
        self._timeout = timeout
        self._max_concurrency = max_concurrency
        self.waited_ms = 0.0
        self._held = False
        self._acquired = False

    async def __aenter__(self) -> "_SlotAcquireContext":
        started = time.perf_counter()
        try:
            if self._timeout and self._timeout > 0:
                # 用标志位记录“是否真的拿到了槽位”：``wait_for`` 超时/任务被取消时，
                # 内层 acquire() 可能已经成功（或即将成功），只看异常类型会漏归还 → 槽位永久泄漏。
                await asyncio.wait_for(self._acquire(), self._timeout)
            else:
                await self._acquire()
        except asyncio.TimeoutError as exc:
            self._release_if_acquired()
            raise AIStudioRateLimitError(
                f"等待并发槽位超过 {self._timeout:g}s"
                f"（rate_limit.max_concurrency={self._max_concurrency}，上一个请求仍在生成）",
                code="queue_timeout",
                param="rate_limit.max_wait_seconds",
                retry_after=5.0,
            ) from exc
        except asyncio.CancelledError:
            self._release_if_acquired()
            raise
        finally:
            self.waited_ms = round((time.perf_counter() - started) * 1000, 1)
        self._held = True
        return self

    async def _acquire(self) -> None:
        await self._semaphore.acquire()
        self._acquired = True

    def _release_if_acquired(self) -> None:
        """超时/取消的竞态兜底：若 ``acquire()`` 已真正拿到槽位则立即归还。"""
        if self._acquired:
            self._acquired = False
            self._semaphore.release()

    async def __aexit__(self, *exc: Any) -> None:
        self.release()

    def release(self) -> None:
        if self._held:
            self._held = False
            self._acquired = False
            self._semaphore.release()


async def _release_slot(slot: Any) -> None:
    """``BackgroundTask`` 兜底：客户端断开导致生成器未被消费时也要归还槽位。"""
    release = getattr(slot, "release", None)
    if callable(release):
        release()
