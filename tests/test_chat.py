"""客户端层测试（任务书 §15 / 阶段 6-7）：真实链路，但上游是 mock。"""

from __future__ import annotations

import httpx
import pytest

from app.protocol.profile import load_profile
from app.upstream.client import AIStudioClient
from app.upstream.errors import AIStudioAuthError, ProtocolUnknownError
from app.upstream.session import SessionState
from app.upstream.transport import UpstreamTransport
from tests.conftest import MOCK_PATH_PROFILE, MOCK_PROFILE, REAL_PROFILE, UNKNOWN_PROFILE, run


def make_client(config, profile, upstream_app, *, session=None) -> AIStudioClient:
    transport = UpstreamTransport(
        config,
        profile,
        session or SessionState.from_cookie_header("mock_session=mock"),
        http_transport=httpx.ASGITransport(app=upstream_app),
    )
    return AIStudioClient(config, profile, transport=transport)


def test_client_send_message_non_stream(config_factory, mock_upstream_app):
    cfg = config_factory()
    profile = load_profile(MOCK_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)

    async def go():
        try:
            return await client.send_message(None, "你好")
        finally:
            await client.aclose()

    chunk = run(go())
    assert "你好" in (chunk.text or "")
    assert chunk.conversation_id
    assert chunk.finish_reason == "stop"
    assert chunk.usage


def test_client_stream_message_yields_incremental_text(config_factory, mock_upstream_app):
    cfg = config_factory()
    profile = load_profile(MOCK_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)

    async def go():
        try:
            stream = client.stream_message(None, "你好")
            return [chunk async for chunk in stream]
        finally:
            await client.aclose()

    chunks = run(go())
    text = "".join(chunk.text or "" for chunk in chunks)
    assert text
    assert any(chunk.conversation_id for chunk in chunks)
    assert any(chunk.finish_reason for chunk in chunks)


def test_client_stream_message_reports_reasoning(config_factory, mock_upstream_app):
    cfg = config_factory()
    profile = load_profile(MOCK_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)

    async def go():
        try:
            return [chunk async for chunk in client.stream_message(None, "请思考一下")]
        finally:
            await client.aclose()

    chunks = run(go())
    assert any(chunk.reasoning for chunk in chunks)


def test_client_raises_protocol_unknown_before_any_http(config_factory, mock_upstream_app):
    """真实档案下（endpoint 仅 inferred）必须在发请求前就报错。"""
    cfg = config_factory(profile=UNKNOWN_PROFILE)
    profile = load_profile(UNKNOWN_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)

    async def go():
        try:
            with pytest.raises(ProtocolUnknownError) as excinfo:
                await client.send_message(None, "你好")
            assert "send_message" in str(excinfo.value)
            for call in (
                client.get_user_info(),
                client.get_history("c1"),
                client.create_conversation("x"),
                client.upload_file("a.png"),
                client.stop_generation("c1"),
            ):
                with pytest.raises(ProtocolUnknownError):
                    await call
        finally:
            await client.aclose()

    run(go())


def test_client_stream_message_protocol_unknown_is_synchronous(config_factory, mock_upstream_app):
    """stream_message 是同步函数：未确认时立刻抛错，便于先返回 501。"""
    cfg = config_factory(profile=UNKNOWN_PROFILE)
    profile = load_profile(UNKNOWN_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)
    with pytest.raises(ProtocolUnknownError):
        client.stream_message(None, "你好")


def test_client_auth_error_on_html_fallback(config_factory):
    from tests.fixtures.mock_upstream import create_mock_app

    cfg = config_factory()
    profile = load_profile(MOCK_PROFILE)
    guarded = create_mock_app(require_cookie="mock_session=correct")
    client = make_client(cfg, profile, guarded, session=SessionState.from_cookie_header("mock_session=wrong"))

    async def go():
        try:
            with pytest.raises(AIStudioAuthError):
                await client.send_message(None, "你好")
        finally:
            await client.aclose()

    run(go())


def test_client_stop_generation_and_models(config_factory, mock_upstream_app):
    cfg = config_factory()
    profile = load_profile(MOCK_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)

    async def go():
        try:
            stopped = await client.stop_generation("conv-1")
            models = await client.list_models()
            user = await client.get_user_info()
            return stopped, models, user
        finally:
            await client.aclose()

    stopped, models, user = run(go())
    assert stopped["status_code"] == 200
    assert [model.id for model in models] == ["mock-chat"]
    assert user["raw"]["data"]["uin"] == "10000"


def test_payload_and_conversation_helpers(config_factory, mock_upstream_app):
    cfg = config_factory()
    profile = load_profile(MOCK_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)

    async def go():
        try:
            key, upstream = await client.resolve_conversation(
                user="u1", messages=[{"role": "user", "content": "hi"}]
            )
            client.remember_conversation(key, "conv-9")
            key2, upstream2 = await client.resolve_conversation(
                user="u1", messages=[{"role": "user", "content": "hi"}]
            )
            return key, upstream, key2, upstream2
        finally:
            await client.aclose()

    key, upstream, key2, upstream2 = run(go())
    assert upstream is None
    assert upstream2 == "conv-9"
    assert key == key2


def test_send_message_without_conversation_creates_one(config_factory, mock_upstream_app):
    """真实档案形状下 send_message(None) 也必须先建会话（不能打到 /chat/）。"""
    cfg = config_factory(profile=MOCK_PATH_PROFILE, conversation={"mode": "stateless"})
    profile = load_profile(MOCK_PATH_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)
    mock_upstream_app.state.calls.clear()

    async def go():
        try:
            return await client.send_message(None, "你好")
        finally:
            await client.aclose()

    chunk = run(go())
    assert chunk.text
    creates = [
        call for call in mock_upstream_app.state.calls if call["path"] == "/api/new-portal/generate/id"
    ]
    assert len(creates) == 1  # 自动建了一次会话


def test_remember_conversation_is_noop_in_stateless(config_factory, mock_upstream_app):
    """stateless 下既然永不复用，就不该写会话缓存（避免无意义的内存占用）。"""
    cfg = config_factory(conversation={"mode": "stateless"})
    profile = load_profile(MOCK_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)

    async def go():
        try:
            key, upstream = await client.resolve_conversation(user="u1")
            client.remember_conversation(key, "conv-9")
            _key2, upstream2 = await client.resolve_conversation(user="u1")
            return upstream, upstream2, client.conversations.get(key)
        finally:
            await client.aclose()

    upstream, upstream2, entry = run(go())
    assert upstream is None
    assert upstream2 is None  # 永远不复用
    assert entry is None  # 且不写缓存


def test_protocol_status_reports_unknown_features(config_factory, mock_upstream_app):
    cfg = config_factory(profile=UNKNOWN_PROFILE)
    profile = load_profile(UNKNOWN_PROFILE)
    client = make_client(cfg, profile, mock_upstream_app)

    async def go():
        try:
            return await client.protocol_status()
        finally:
            await client.aclose()

    status = run(go())
    assert "unknown_features" in status
    assert status["upload"]["implemented"] is False
    assert status["upload"]["evidence_needed"]
