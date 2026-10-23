"""流式解析：SSE / NDJSON / 事件翻译 / JSON 路径。"""

from __future__ import annotations

import pytest

from app.protocol.profile import load_profile
from app.upstream.errors import AIStudioStreamError, ProtocolUnknownError
from app.upstream.streaming import (
    EventInterpreter,
    NDJSONParser,
    SSEParser,
    StreamEvent,
    extract_non_stream,
    extract_path,
    first_path,
    get_parser,
    iter_chunks,
)
from tests.conftest import MOCK_PROFILE, REAL_PROFILE, UNKNOWN_PROFILE, run


def test_sse_parser_groups_events_and_handles_comments():
    parser = SSEParser()
    events: list[StreamEvent] = []
    for line in (
        ": heartbeat",
        "event: message_start",
        'data: {"a": 1}',
        "",
        "data: [DONE]",
        "",
    ):
        events.extend(parser.feed(line))
    events.extend(parser.finish())
    assert len(events) == 2
    assert events[0].name == "message_start"
    assert events[0].data == '{"a": 1}'
    assert events[1].data == "[DONE]"


def test_sse_parser_supports_multiline_data_and_fields():
    parser = SSEParser()
    events = parser.feed("id: 7")
    events += parser.feed("retry: 3000")
    events += parser.feed("data: line1")
    events += parser.feed("data: line2")
    events += parser.feed("")
    assert len(events) == 1
    assert events[0].data == "line1\nline2"
    assert events[0].id == "7"
    assert events[0].retry == 3000


def test_ndjson_parser_and_parser_lookup():
    parser = NDJSONParser()
    assert parser.feed('{"a": 1}')[0].data == '{"a": 1}'
    assert parser.feed("   ") == []
    assert get_parser("sse") is SSEParser
    with pytest.raises(ProtocolUnknownError):
        get_parser("unknown")
    with pytest.raises(ProtocolUnknownError):
        get_parser(None)


def test_extract_path_supports_indexes_and_missing_keys():
    doc = {"delta": {"content": "hi"}, "choices": [{"finish_reason": "stop"}]}
    assert extract_path(doc, "$.delta.content") == "hi"
    assert extract_path(doc, "$.choices[0].finish_reason") == "stop"
    assert extract_path(doc, "$.choices[5].finish_reason") is None
    assert extract_path(doc, "$.nope.deep") is None
    assert extract_path(doc, "delta.content") is None
    assert first_path(doc, ["$.missing", "$.delta.content"]) == "hi"


def test_event_interpreter_maps_fields_by_profile():
    spec = load_profile(MOCK_PROFILE).stream
    interpreter = EventInterpreter(spec)

    chunk = interpreter.chunks(StreamEvent(data='{"delta": {"content": "你好"}}'))[0]
    assert chunk.text == "你好"

    chunk = interpreter.chunks(StreamEvent(data='{"delta": {"reasoning": "想一下"}}'))[0]
    assert chunk.reasoning == "想一下"

    finish = interpreter.chunks(StreamEvent(data='{"finish_reason": "stop", "usage": {"total_tokens": 3}}'))[0]
    assert finish.finish_reason == "stop"
    assert finish.usage == {"total_tokens": 3}

    assert interpreter.chunks(StreamEvent(name="ping", data="{}")) == []
    assert interpreter.is_done(StreamEvent(data="[DONE]"))
    assert not interpreter.is_done(StreamEvent(data='{"a": 1}'))

    raw = interpreter.chunks(StreamEvent(data="not-json"))
    assert raw[0].raw_event == "not-json"


def test_iter_chunks_end_to_end_and_empty_stream_error():
    spec = load_profile(MOCK_PROFILE).stream

    async def lines():
        for line in (
            'data: {"delta": {"content": "你好"}}',
            "",
            'data: {"delta": {"content": "！"}}',
            "",
            "data: [DONE]",
            "",
        ):
            yield line

    async def collect():
        return [chunk async for chunk in iter_chunks(lines(), spec)]

    chunks = run(collect())
    assert "".join(chunk.text or "" for chunk in chunks) == "你好！"

    async def empty():
        if False:  # pragma: no cover
            yield ""
        return

    async def collect_empty():
        return [chunk async for chunk in iter_chunks(empty(), spec)]

    with pytest.raises(AIStudioStreamError):
        run(collect_empty())


def test_extract_non_stream_uses_response_schema():
    profile = load_profile(MOCK_PROFILE)
    payload = {
        "data": {
            "content": "回答",
            "conversation_id": "c1",
            "message_id": "m1",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }
    }
    chunk = extract_non_stream(payload, profile.response_schema)
    assert chunk.text == "回答"
    assert chunk.conversation_id == "c1"
    assert chunk.usage == {"prompt_tokens": 1, "completion_tokens": 2}


def test_real_profile_stream_is_unknown():
    profile = load_profile(UNKNOWN_PROFILE)
    with pytest.raises(ProtocolUnknownError):
        profile.require_stream()


def test_real_captured_sse_frames_are_parsed():
    """用抓包（session.har）里的真实 SSE 帧验证解析器与 type 判别。"""
    profile = load_profile(REAL_PROFILE)
    spec = profile.require_stream()
    lines = [
        ": speech_type",
        "data: thought",
        "",
        'data: {"type":"traceId","traceId":"660de976ab7b50f5edb9ada05c5314b6"}',
        "",
        'data: {"type":"thought","title":"思考中...","msg":"好的","id":"dalbtdk2c3m0skb4ehv0"}',
        "",
        'data: {"type":"thought","title":"已深度思考","cost":10.117,"id":"dalbtdk2c3m0skb4ehv0"}',
        "",
        "event: speech_type",
        "data: text",
        "",
        'data: {"type":"text","msg":"我"}',
        "",
        'data: {"type":"text","msg":"没问题"}',
        "",
        'data: {"type":"meta","chatRecordId":"dalbtdk2c3m0skb4ehv0","conversationId":"dalbtdc3mae40tp140","empty":false,"fileTooLong":false}',
        "",
        "data: [DONE]",
        "",
    ]

    async def gen():
        for line in lines:
            yield line

    async def collect():
        return [c async for c in iter_chunks(gen(), spec)]

    chunks = run(collect())
    assert "".join(c.text or "" for c in chunks) == "我没问题"
    assert "".join(c.reasoning or "" for c in chunks) == "好的"
    assert any(c.conversation_id == "dalbtdc3mae40tp140" for c in chunks)
    assert any(c.message_id == "dalbtdk2c3m0skb4ehv0" for c in chunks)
