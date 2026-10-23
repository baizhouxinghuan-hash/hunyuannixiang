"""服务端测试（任务书 §16 / §17 / §25 / §27 / §41 D-G）。

用 ASGITransport 直接调用我们的 FastAPI 应用，上游是 mock（零网络）。
"""

from __future__ import annotations

import asyncio
import json

import httpx

from app.config import ConversationConfig
from tests.conftest import MOCK_PATH_PROFILE, REAL_PROFILE, UNKNOWN_PROFILE, local_client, run


def post_chat(app, payload: dict, headers: dict | None = None):
    async def go():
        async with local_client(app) as client:
            return await client.post("/v1/chat/completions", json=payload, headers=headers or {})

    return run(go())


def get_json(app, path: str):
    async def go():
        async with local_client(app) as client:
            return await client.get(path)

    return run(go())


#: 测试用工具定义（OpenAI tools 形状）
WEATHER_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的天气",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名"}},
                "required": ["city"],
            },
        },
    }
]


def test_health_reports_protocol_and_unknown_features(make_app):
    app = make_app()
    response = get_json(app, "/health?probe=false")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "ok_with_unknown_protocol")
    assert body["protocol"]["fingerprint"]
    assert "upload" in body
    assert body["upload"]["implemented"] is False


def test_health_with_real_profile_reports_unknown(make_app):
    """回归：真实档案（存在大量 unknown）下 /health 不能崩。"""
    app = make_app(profile=UNKNOWN_PROFILE)
    response = get_json(app, "/health?probe=false")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok_with_unknown_protocol"
    assert "stream" in body["unknown_features"]
    assert "endpoint:send_message" in body["unknown_features"]


def test_health_reports_degraded_without_session(make_app, tmp_path):
    """回归：配了 storage_state 但文件不存在时，/health 必须报告 degraded（而不是崩/假装正常）。"""
    app = make_app(auth={"mode": "storage_state", "storage_state": str(tmp_path / "missing.json")})
    response = get_json(app, "/health?probe=false")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["authenticated"] is False
    assert "auth_error" in body
    assert body["session"]["loaded"] is False


def test_health_probe_reports_authenticated(make_app, tmp_path):
    """auth.mode=storage_state 且存在会话文件时，probe 应报告已认证。

    注意：会话文件写到 tmp_path，**绝不能写进项目的 secrets/**（曾因此污染过真实目录）。
    """
    import json as _json
    import time as _time

    state = tmp_path / "storage_state.json"
    state.write_text(
        _json.dumps(
            {
                "cookies": [
                    {
                        "name": "mock_session",
                        "value": "mock-value",
                        "domain": "mock-upstream",
                        "path": "/",
                        "expires": _time.time() + 3600,
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )

    app = make_app(auth={"mode": "storage_state", "storage_state": str(state)})
    response = get_json(app, "/health?probe=true")
    body = response.json()
    assert "authenticated" in body
    assert body["session"]["loaded"] is True
    assert body["authenticated"] is True


def test_models_endpoint(make_app):
    app = make_app()
    response = get_json(app, "/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert [model["id"] for model in body["data"]] == ["mock-chat"]


def test_models_endpoint_without_confirmed_models(make_app):
    app = make_app(profile=UNKNOWN_PROFILE)
    body = get_json(app, "/v1/models").json()
    assert body["data"] == []
    assert "x_warning" in body


def test_chat_completions_non_stream(make_app):
    app = make_app()
    response = post_chat(
        app,
        {"model": "mock-chat", "messages": [{"role": "user", "content": "你好"}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "你好" in body["choices"][0]["message"]["content"]
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]


def test_chat_completions_stream_sse(make_app):
    app = make_app()
    response = post_chat(
        app,
        {"model": "mock-chat", "stream": True, "messages": [{"role": "user", "content": "你好"}]},
    )
    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers["content-type"]
    text = response.text
    assert "chat.completion.chunk" in text
    assert text.strip().endswith("data: [DONE]")
    payloads = [
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: ") and "[DONE]" not in line
    ]
    assert payloads[0]["choices"][0]["delta"]["role"] == "assistant"
    assert any(chunk["choices"][0]["delta"].get("content") for chunk in payloads)
    assert any(chunk["choices"][0]["finish_reason"] == "stop" for chunk in payloads)


def test_chat_completions_stream_reasoning(make_app):
    app = make_app()
    response = post_chat(
        app,
        {"model": "mock-chat", "stream": True, "messages": [{"role": "user", "content": "请思考一下"}]},
    )
    assert "reasoning_content" in response.text


def test_chat_completions_reasoning_drop_mode(make_app):
    app = make_app(reasoning={"mode": "drop"})
    response = post_chat(
        app,
        {"model": "mock-chat", "stream": True, "messages": [{"role": "user", "content": "请思考一下"}]},
    )
    assert "reasoning_content" not in response.text


def test_multi_turn_keeps_context(make_app):
    """任务书 §41 F：先“我叫小明”，再问“我叫什么”。"""
    app = make_app()
    first = post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "我的名字叫小明"}],
            "user": "tester",
        },
    )
    assert first.status_code == 200
    assert "小明" in first.json()["choices"][0]["message"]["content"]

    second = post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "我叫什么？"}],
            "user": "tester",
        },
    )
    assert "小明" in second.json()["choices"][0]["message"]["content"]


def test_multi_turn_stateless_mode_has_no_context(make_app):
    """stateless 模式每次新建上游会话（prompt_only 时上游自然没有上下文）。"""
    app = make_app(conversation={"mode": "stateless"})
    post_chat(app, {"model": "mock-chat", "messages": [{"role": "user", "content": "我的名字叫小明"}]})
    second = post_chat(app, {"model": "mock-chat", "messages": [{"role": "user", "content": "我叫什么？"}]})
    assert "小明" not in second.json()["choices"][0]["message"]["content"]


def test_default_conversation_mode_is_stateless():
    """产品默认行为：每次请求都新建上游会话 —— 本次回答与上一次无关。"""
    assert ConversationConfig().mode == "stateless"


def test_stateless_creates_new_upstream_conversation_every_request(make_app, mock_upstream_app):
    """stateless：每次请求都调 generate/id 新建会话，同一个 user 也不复用。

    使用带 {conversation_id} 路径的档案（形状同抓包确认的真实协议）。
    """
    app = make_app(profile=MOCK_PATH_PROFILE, conversation={"mode": "stateless"})
    mock_upstream_app.state.calls.clear()
    payload = {
        "model": "mock-chat",
        "messages": [{"role": "user", "content": "你好"}],
        "user": "tester",
    }

    first = post_chat(app, payload)
    second = post_chat(app, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    creates = [
        call for call in mock_upstream_app.state.calls if call["path"] == "/api/new-portal/generate/id"
    ]
    assert len(creates) == 2  # 两次都新建
    assert first.json()["conversation_id"] != second.json()["conversation_id"]
    sends = [
        call["path"]
        for call in mock_upstream_app.state.calls
        if call["path"].startswith("/api/new-portal/chat/")
    ]
    assert len(sends) == 2
    assert sends[0] != sends[1]  # 两次发往不同的上游会话


def test_auto_mode_reuses_upstream_conversation(make_app, mock_upstream_app):
    """对照（可选的多轮模式）：auto 下同一个 user 复用会话，第二次不再新建。"""
    app = make_app(profile=MOCK_PATH_PROFILE, conversation={"mode": "auto"})
    mock_upstream_app.state.calls.clear()

    first = post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "我的名字叫小明"}],
            "user": "tester",
        },
    )
    second = post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "我叫什么？"}],
            "user": "tester",
        },
    )

    creates = [
        call for call in mock_upstream_app.state.calls if call["path"] == "/api/new-portal/generate/id"
    ]
    assert len(creates) == 1  # 只建了一次
    assert first.json()["conversation_id"] == second.json()["conversation_id"]
    assert "小明" in second.json()["choices"][0]["message"]["content"]


def test_effort_default_and_reasoning_effort_override(make_app, mock_upstream_app):
    """思考强度：默认取 config.model.effort；客户端 reasoning_effort 可覆盖。"""
    app = make_app()
    payload = {"model": "mock-chat", "messages": [{"role": "user", "content": "你好"}]}

    mock_upstream_app.state.calls.clear()
    post_chat(app, payload)
    assert mock_upstream_app.state.calls[0]["payload"]["effort"] == "no_think"

    mock_upstream_app.state.calls.clear()
    post_chat(app, {**payload, "reasoning_effort": "high"})
    assert mock_upstream_app.state.calls[0]["payload"]["effort"] == "high"

    # minimal/low/medium -> no_think
    high_default = make_app(model={"effort": "high"})
    mock_upstream_app.state.calls.clear()
    post_chat(high_default, {**payload, "reasoning_effort": "low"})
    assert mock_upstream_app.state.calls[0]["payload"]["effort"] == "no_think"

    # 不传 -> 用配置值
    mock_upstream_app.state.calls.clear()
    post_chat(high_default, payload)
    assert mock_upstream_app.state.calls[0]["payload"]["effort"] == "high"


def test_nonstream_timeout_returns_504(make_app):
    """非流式等太久 -> 504 upstream_timeout（而不是让客户端自己超时断连）。"""
    app = make_app(upstream={"nonstream_timeout": 0.3})
    response = post_chat(
        app, {"model": "mock-chat", "messages": [{"role": "user", "content": "SLEEP:2 慢请求"}]}
    )
    assert response.status_code == 504
    body = response.json()
    assert body["error"]["code"] == "upstream_timeout"
    assert "nonstream_timeout" in body["error"]["message"]


def test_queue_wait_timeout_returns_429(make_app):
    """并发=1 时排太久的请求 -> 429 queue_timeout + Retry-After（不无限等待）。"""
    app = make_app(rate_limit={"enabled": True, "max_concurrency": 1, "max_wait_seconds": 0.3})

    async def go():
        async with local_client(app) as client:
            first = asyncio.create_task(
                client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "mock-chat",
                        "messages": [{"role": "user", "content": "SLEEP:1 慢请求"}],
                    },
                )
            )
            await asyncio.sleep(0.05)
            second = await client.post(
                "/v1/chat/completions",
                json={"model": "mock-chat", "messages": [{"role": "user", "content": "第二个请求"}]},
            )
            await first
            return second

    second = run(go())
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "queue_timeout"


def test_fallback_text_mode_uses_reasoning_when_content_empty(make_app, mock_sse_profile):
    """上游只回思考、正文为空时，fallback_text 用思考兜底（本地策略，非上游行为）。"""
    app = make_app(profile=mock_sse_profile, reasoning={"mode": "fallback_text"})
    response = post_chat(
        app, {"model": "mock-chat", "messages": [{"role": "user", "content": "只思考"}]}
    )
    assert response.status_code == 200
    message = response.json()["choices"][0]["message"]
    assert "只有思考" in message["content"]
    assert "reasoning_content" not in message

    # 对照：默认 passthrough 下正文就是空的（客户端会看到空白）
    plain = make_app(profile=mock_sse_profile)
    raw = post_chat(
        plain, {"model": "mock-chat", "messages": [{"role": "user", "content": "只思考"}]}
    )
    body = raw.json()["choices"][0]["message"]
    assert body["content"] == ""
    assert "只有思考" in (body.get("reasoning_content") or "")


def test_system_persona_is_folded_into_upstream_prompt(make_app, mock_upstream_app, mock_sse_profile):
    """人格（system 消息）必须进入发往上游的 prompt —— 这是 AstrBot 人格丢失的修复点。"""
    app = make_app(profile=mock_sse_profile)
    mock_upstream_app.state.calls.clear()
    post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [
                {"role": "system", "content": "你是小白，说话要带喵"},
                {"role": "user", "content": "你好"},
            ],
        },
    )
    outgoing = str(mock_upstream_app.state.calls[0]["payload"]["prompt"])
    assert "系统：你是小白，说话要带喵" in outgoing
    assert "用户：你好" in outgoing


def test_history_is_folded_into_prompt(make_app, mock_upstream_app, mock_sse_profile):
    """多轮：客户端带来的历史也要进 prompt（上游侧仍是每次新会话）。"""
    app = make_app(profile=mock_sse_profile)
    mock_upstream_app.state.calls.clear()
    post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [
                {"role": "user", "content": "我最喜欢的颜色是蓝色"},
                {"role": "assistant", "content": "记住了"},
                {"role": "user", "content": "我喜欢什么颜色"},
            ],
        },
    )
    outgoing = str(mock_upstream_app.state.calls[0]["payload"]["prompt"])
    assert "用户：我最喜欢的颜色是蓝色" in outgoing
    assert "助手：记住了" in outgoing
    assert outgoing.rstrip().endswith("用户：我喜欢什么颜色")


def test_tools_are_bridged_to_standard_tool_calls(make_app, mock_upstream_app, mock_sse_profile):
    """工具桥接：模型输出 JSON -> 标准 OpenAI tool_calls + finish_reason=tool_calls。"""
    app = make_app(profile=mock_sse_profile)
    mock_upstream_app.state.calls.clear()
    response = post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "TOOLJSON 北京天气怎么样"}],
            "tools": WEATHER_TOOL,
        },
    )
    assert response.status_code == 200
    assert response.headers.get("x-aistudio-warning") == "tools_bridged"
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "北京"}
    outgoing = str(mock_upstream_app.state.calls[0]["payload"]["prompt"])
    assert "【工具调用说明】" in outgoing
    assert "get_weather" in outgoing


def test_tools_bridge_falls_back_to_text_when_model_answers_normally(make_app, mock_sse_profile):
    """桥接失败要回退成普通文本，绝不因为桥接而丢回答。"""
    app = make_app(profile=mock_sse_profile)
    response = post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "你好"}],
            "tools": WEATHER_TOOL,
        },
    )
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert "tool_calls" not in choice["message"]
    assert choice["message"]["content"]


def test_tools_bridge_stream_emits_tool_call_delta(make_app, mock_sse_profile):
    app = make_app(profile=mock_sse_profile)
    response = post_chat(
        app,
        {
            "model": "mock-chat",
            "stream": True,
            "messages": [{"role": "user", "content": "TOOLJSON 北京天气"}],
            "tools": WEATHER_TOOL,
        },
    )
    assert '"tool_calls"' in response.text
    assert "get_weather" in response.text
    assert "data: [DONE]" in response.text


def test_diagnostic_header_reports_message_stats(make_app, mock_sse_profile):
    """``x-aistudio-messages: <总条数>/<system条数>/<历史条数>`` —— 排查人格/历史有没有进 prompt。"""
    app = make_app(profile=mock_sse_profile)
    response = post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [
                {"role": "system", "content": "你是小白"},
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "第一个回答"},
                {"role": "user", "content": "第二问"},
            ],
        },
    )
    assert response.status_code == 200
    # 4 条 = system(1) + user + assistant + 最后一条 user；历史 = 2（第一条 user + assistant）
    assert response.headers.get("x-aistudio-messages") == "4/1/2"

    # 对照：只有一条 user 时，历史为 0
    plain = post_chat(
        app, {"model": "mock-chat", "messages": [{"role": "user", "content": "只有一句"}]}
    )
    assert plain.headers.get("x-aistudio-messages") == "1/0/0"


def test_tools_mode_off_keeps_honest_warning(make_app, mock_upstream_app, mock_sse_profile):
    """tools.mode=off：不桥接，保持「未透传」告警（不伪造 tool_calls）。"""
    app = make_app(profile=mock_sse_profile, tools={"mode": "off"})
    mock_upstream_app.state.calls.clear()
    response = post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "TOOLJSON 北京天气"}],
            "tools": WEATHER_TOOL,
        },
    )
    assert response.headers.get("x-aistudio-warning") == "tools_not_passed"
    assert "tool_calls" not in response.json()["choices"][0]["message"]
    outgoing = str(mock_upstream_app.state.calls[0]["payload"]["prompt"])
    assert "【工具调用说明】" not in outgoing


def test_error_mapping_upstream_429(make_app):
    app = make_app()
    response = post_chat(app, {"model": "mock-chat", "messages": [{"role": "user", "content": "麻烦返回 429"}]})
    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_error"


def test_error_mapping_upstream_500(make_app):
    app = make_app()
    response = post_chat(app, {"model": "mock-chat", "messages": [{"role": "user", "content": "麻烦返回 500"}]})
    assert response.status_code == 500  # 透传上游 5xx 状态码
    assert response.json()["error"]["type"] == "server_error"


def test_error_mapping_html_means_not_authenticated(make_app):
    app = make_app()
    response = post_chat(app, {"model": "mock-chat", "messages": [{"role": "user", "content": "触发 401 场景"}]})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "authentication_error"
    assert "capture.py" in body["error"]["message"] or "storage_state" in body["error"]["message"]


def test_protocol_unknown_returns_501_without_fake_answer(make_app):
    """未确认协议时必须显式报错（Batch A 验收点 4）。"""
    app = make_app(profile=UNKNOWN_PROFILE)
    response = post_chat(app, {"model": "x", "messages": [{"role": "user", "content": "你好"}]})
    assert response.status_code == 501
    body = response.json()
    assert body["error"]["code"] == "protocol_unknown"
    assert "protocol/current/profile.yaml" in body["error"]["message"]
    assert "choices" not in body


def test_protocol_unknown_for_stream_returns_501_not_partial_stream(make_app):
    app = make_app(profile=UNKNOWN_PROFILE)
    response = post_chat(app, {"model": "x", "stream": True, "messages": [{"role": "user", "content": "你好"}]})
    assert response.status_code == 501
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_stream_disconnect_ends_stream_cleanly(make_app):
    """上游中途断流：客户端侧仍以 [DONE] 收尾，不抛 500。"""
    app = make_app()
    response = post_chat(
        app,
        {"model": "mock-chat", "stream": True, "messages": [{"role": "user", "content": "麻烦断流一次"}]},
    )
    assert response.status_code == 200
    assert "data: [DONE]" in response.text


def test_validation_errors(make_app):
    app = make_app()
    assert post_chat(app, {"model": "mock-chat", "messages": []}).status_code == 400
    assert (
        post_chat(app, {"model": "mock-chat", "messages": [{"role": "user", "content": "hi"}], "n": 2}).status_code
        == 400
    )
    response = post_chat(app, {"model": "mock-chat", "messages": [{"role": "assistant", "content": "hi"}]})
    assert response.status_code == 400


def test_unknown_model_returns_protocol_unknown(make_app):
    app = make_app()
    response = post_chat(app, {"model": "does-not-exist", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "protocol_unknown"


def test_api_key_enforcement(make_app):
    app = make_app(server={"api_keys": ["sk-local-secret"]})
    response = post_chat(app, {"model": "mock-chat", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 401  # 与 OpenAI 一致：无效 Key -> 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert response.json()["error"]["type"] == "invalid_request_error"

    ok = post_chat(
        app,
        {"model": "mock-chat", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-local-secret"},
    )
    assert ok.status_code == 200


def test_unsupported_params_produce_warning_header(make_app):
    app = make_app()
    response = post_chat(
        app,
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "hi"}],
            "top_p": 0.5,
            "tools": [{"type": "function", "function": {"name": "x"}}],
        },
    )
    assert response.status_code == 200
    warning = response.headers.get("x-aistudio-warning", "")
    assert "tools" in warning
    assert "top_p" in warning


def test_concurrency_limit_serializes_requests(make_app):
    """max_concurrency=1 时两个并发请求都能正常完成（内部串行化）。"""
    import asyncio

    app = make_app(rate_limit={"enabled": True, "max_concurrency": 1})

    async def go():
        async with local_client(app) as client:
            first, second = await asyncio.gather(
                client.post(
                    "/v1/chat/completions",
                    json={"model": "mock-chat", "messages": [{"role": "user", "content": "并发 1"}], "user": "p1"},
                ),
                client.post(
                    "/v1/chat/completions",
                    json={"model": "mock-chat", "messages": [{"role": "user", "content": "并发 2"}], "user": "p2"},
                ),
            )
            return first.status_code, second.status_code

    assert run(go()) == (200, 200)


def test_v1_protocol_endpoint(make_app):
    app = make_app()
    body = get_json(app, "/v1/protocol").json()
    assert body["protocol_version"]
    assert "confirmed_endpoints" in body


def test_stream_ends_with_done_and_usage_chunk(make_app):
    app = make_app()
    response = post_chat(
        app,
        {
            "model": "mock-chat",
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert '"usage"' in response.text
    assert response.text.strip().endswith("data: [DONE]")

def test_models_endpoint_requires_api_key(make_app):
    app = make_app(server={"api_keys": ["sk-local-secret"]})

    async def go():
        async with local_client(app) as client:
            missing = await client.get("/v1/models")
            ok = await client.get("/v1/models", headers={"Authorization": "Bearer sk-local-secret"})
            return missing, ok

    missing, ok = run(go())
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_api_key"
    assert ok.status_code == 200


def test_retrieve_model_endpoint(make_app):
    """OpenAI 兼容：/v1/models/{model_id}（部分客户端会调用）。"""
    app = make_app()
    body = get_json(app, "/v1/models/mock-chat").json()
    assert body["id"] == "mock-chat"
    assert body["object"] == "model"

    missing = get_json(app, "/v1/models/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "model_not_found"


def test_retrieve_model_without_confirmed_models(make_app):
    app = make_app(profile=UNKNOWN_PROFILE)
    response = get_json(app, "/v1/models/anything")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_models_endpoint_with_captured_profile(make_app):
    """抓包回填后 /v1/models 返回真实模型（不碰上游网络）。"""
    app = make_app(profile=REAL_PROFILE)
    body = get_json(app, "/v1/models").json()
    assert [m["id"] for m in body["data"]] == ["hy4-preview"]
    assert "x_warning" not in body

    retrieved = get_json(app, "/v1/models/hy4-preview").json()
    assert retrieved["id"] == "hy4-preview"
