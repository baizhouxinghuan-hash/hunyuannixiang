"""OpenAI 适配层测试（任务书 §17/§18/§21）。"""

from __future__ import annotations

import json

import pytest

from app.adapters.openai import (
    ChatResult,
    accumulate,
    chunk_to_openai_stream,
    collect_passthrough,
    content_to_text,
    final_stream_chunk,
    first_stream_chunk,
    normalize_finish_reason,
    result_to_openai_response,
    to_upstream_messages,
)
from app.protocol.profile import load_profile
from app.upstream.errors import ProtocolUnknownError
from app.upstream.streaming import ChatChunk
from tests.conftest import MOCK_PROFILE, REAL_PROFILE


def test_content_to_text_variants():
    assert content_to_text("abc") == "abc"
    assert content_to_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "ab"
    assert content_to_text(None) == ""


def test_accumulate_preserves_reasoning_and_usage():
    chunks = [
        ChatChunk(text="你", reasoning="想"),
        ChatChunk(text="好", reasoning="一下"),
        ChatChunk(finish_reason="stop", usage={"total_tokens": 5}, conversation_id="c1"),
    ]
    result = accumulate(chunks)
    assert result.text == "你好"
    assert result.reasoning == "想一下"
    assert result.finish_reason == "stop"
    assert result.usage == {"total_tokens": 5}
    assert result.conversation_id == "c1"


def test_result_to_openai_response_shapes_and_reasoning_modes():
    result = ChatResult(text="回答", reasoning="思考", finish_reason="stop", usage={"total_tokens": 7})
    body = result_to_openai_response(result, model="mock-chat", request_id="chatcmpl-1", created=0)
    assert body["id"] == "chatcmpl-1"
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "回答"
    assert body["choices"][0]["message"]["reasoning_content"] == "思考"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 7

    dropped = result_to_openai_response(result, model="m", reasoning_mode="drop")
    assert "reasoning_content" not in dropped["choices"][0]["message"]

    as_text = result_to_openai_response(result, model="m", reasoning_mode="as_text")
    assert as_text["choices"][0]["message"]["content"] == "思考\n\n回答"


def test_usage_is_null_when_upstream_has_none():
    body = result_to_openai_response(ChatResult(text="x"), model="m")
    assert body["usage"] is None


def test_chunk_to_openai_stream_deltas_and_finish():
    chunk = ChatChunk(text="你好", reasoning="想")
    payload = chunk_to_openai_stream(chunk, model="m", request_id="id", created=0)
    assert payload["object"] == "chat.completion.chunk"
    assert payload["choices"][0]["delta"] == {"content": "你好", "reasoning_content": "想"}

    finish = chunk_to_openai_stream(ChatChunk(finish_reason="length"), model="m", request_id="id", created=0)
    assert finish["choices"][0]["finish_reason"] == "length"
    assert finish["choices"][0]["delta"] == {}

    assert chunk_to_openai_stream(ChatChunk(usage={"total_tokens": 1}), model="m", request_id="id", created=0) is None
    with_usage = chunk_to_openai_stream(
        ChatChunk(usage={"total_tokens": 1}), model="m", request_id="id", created=0, include_usage=True
    )
    assert with_usage["usage"]["total_tokens"] == 1


def test_first_and_final_stream_chunk_and_sse_encoding():
    first = first_stream_chunk(model="m", request_id="id", created=0)
    assert first["choices"][0]["delta"] == {"role": "assistant"}
    final = final_stream_chunk(model="m", request_id="id", created=0)
    assert final["choices"][0]["finish_reason"] == "stop"


def test_tool_call_delta_wrapping():
    payload = chunk_to_openai_stream(
        ChatChunk(tool_call={"id": "call_1", "function": {"name": "search", "arguments": {"q": "x"}}}),
        model="m",
        request_id="id",
        created=0,
    )
    call = payload["choices"][0]["delta"]["tool_calls"][0]
    assert call["function"]["name"] == "search"
    assert json.loads(call["function"]["arguments"]) == {"q": "x"}


def test_normalize_finish_reason():
    assert normalize_finish_reason("max_tokens") == "length"
    assert normalize_finish_reason("finished") == "stop"
    assert normalize_finish_reason(None) == "stop"
    assert normalize_finish_reason("weird-value") == "stop"


def test_to_upstream_messages_folds_system_by_default():
    profile = load_profile(MOCK_PROFILE)
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在"},
    ]
    out = to_upstream_messages(messages, profile)
    assert out[0]["role"] == "user"
    assert out[0]["content"].startswith("你是助手")
    assert out[1] == {"role": "assistant", "content": "在"}


def test_to_upstream_messages_as_message_mode():
    profile = load_profile(MOCK_PROFILE)
    data = profile.model_dump()
    data["request_schema"]["system_mode"] = "as_message"
    from app.protocol.profile import Profile

    out = to_upstream_messages([{"role": "system", "content": "s"}], Profile.model_validate(data))
    assert out == [{"role": "system", "content": "s"}]


def test_image_parts_rejected_until_confirmed():
    profile = load_profile(REAL_PROFILE)
    with pytest.raises(ProtocolUnknownError) as excinfo:
        to_upstream_messages(
            [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://x/y.png"}}]}],
            profile,
        )
    assert "image_input" in str(excinfo.value)


def test_collect_passthrough_only_whitelisted_params():
    profile = load_profile(MOCK_PROFILE)
    payload = {"temperature": 0.5, "top_p": 0.9, "model": "m", "messages": [], "stream": True}
    assert collect_passthrough(payload, profile) == {"temperature": 0.5}

    real = load_profile(REAL_PROFILE)
    assert collect_passthrough(payload, real) == {}
