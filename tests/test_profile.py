"""协议档案：加载、状态严格性、指纹与变化检测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.protocol.profile import (
    Profile,
    compare_snapshot,
    load_profile,
    write_snapshot,
)
from app.upstream.chat import build_send_payload, history_mode, render_messages_text, render_template
from app.upstream.errors import ProtocolUnknownError
from tests.conftest import MOCK_PROFILE, REAL_PROFILE, UNKNOWN_PROFILE


def test_mock_profile_endpoints_are_confirmed():
    profile = load_profile(MOCK_PROFILE)
    spec = profile.require_endpoint("send_message")
    assert spec.method == "POST"
    assert spec.path.startswith("/api/")
    assert profile.require_stream().transport == "sse"


def test_real_profile_keeps_unknown_as_unknown():
    """真实档案里，未抓包确认的端点必须显式报错（绝不猜测）。"""
    profile = load_profile(UNKNOWN_PROFILE)
    assert profile.upstream.base_url.status == "confirmed"
    with pytest.raises(ProtocolUnknownError) as excinfo:
        profile.require_endpoint("send_message")
    assert "endpoint:send_message" in str(excinfo.value)
    assert "inferred" in excinfo.value.message

    unknown = profile.unknown_features()
    assert "stream" in unknown
    assert any(item.startswith("endpoint:") for item in unknown)


def test_real_profile_covers_required_endpoint_names():
    profile = load_profile(UNKNOWN_PROFILE)
    for name in (
        "send_message",
        "stop_generation",
        "history",
        "models",
        "user_info",
        "upload",
        "create_conversation",
        "message_detail",
    ):
        assert name in profile.endpoints, name


def test_resolve_model_mapping_and_errors():
    profile = load_profile(MOCK_PROFILE)
    assert profile.resolve_model("mock-chat") == "mock-chat-v1"
    assert profile.resolve_model(None) == "mock-chat-v1"
    assert profile.resolve_model("mock-chat", override={"mock-chat": "override-v2"}) == "override-v2"
    with pytest.raises(ProtocolUnknownError):
        profile.resolve_model("not-exists")


def test_render_template_requires_variables():
    with pytest.raises(ProtocolUnknownError) as excinfo:
        render_template({"prompt": "{unknown_var}"}, {"prompt": "x"})
    assert "unknown_var" in excinfo.value.message
    assert render_template({"stream": "{stream}"}, {"stream": True}) == {"stream": True}
    assert render_template("literal", {}) == "literal"


def test_render_messages_text_keeps_persona_and_history():
    """人格（system）与历史必须进 prompt —— 这是 AstrBot「人格丢失」的修复点。"""
    text = render_messages_text(
        [
            {"role": "system", "content": "你是小白，说话要带喵"},
            {"role": "user", "content": "我的名字叫小明"},
            {"role": "assistant", "content": "记住啦"},
            {"role": "user", "content": "我叫什么"},
        ]
    )
    assert text.split("\n\n") == [
        "系统：你是小白，说话要带喵",
        "用户：我的名字叫小明",
        "助手：记住啦",
        "用户：我叫什么",
    ]


def test_render_messages_text_handles_parts_and_tool_role():
    text = render_messages_text(
        [
            {"role": "user", "content": [{"type": "text", "text": "看这个"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "晴"},
        ]
    )
    assert "用户：看这个" in text
    assert "工具结果(call_1)：晴" in text


def test_render_messages_text_truncates_history_but_keeps_system_and_last():
    messages = [{"role": "system", "content": "人格"}]
    messages += [{"role": "user", "content": f"历史{i}" + "x" * 40} for i in range(10)]
    messages.append({"role": "user", "content": "当前提问"})
    text = render_messages_text(messages, max_chars=120)
    assert text.startswith("系统：人格")
    assert text.endswith("用户：当前提问")
    assert "历史0" not in text


def test_render_messages_text_empty_returns_blank():
    assert render_messages_text(None) == ""
    assert render_messages_text([]) == ""


def test_build_send_payload_and_history_mode():
    profile = load_profile(MOCK_PROFILE)
    payload = build_send_payload(
        profile, conversation_id=None, model="mock-chat-v1", prompt="你好", stream=True
    )
    assert payload == {
        "conversation_id": None,
        "model": "mock-chat-v1",
        "prompt": "你好",
        "stream": True,
        "effort": "no_think",  # 默认思考强度（config.model.effort 的默认值）
    }
    assert history_mode(profile) == "prompt_only"

    # effort 可显式覆盖（客户端 reasoning_effort 映射后传进来）
    fast = build_send_payload(
        profile, conversation_id=None, model="mock-chat-v1", prompt="你好", stream=False, effort="high"
    )
    assert fast["effort"] == "high"

    real = load_profile(UNKNOWN_PROFILE)
    with pytest.raises(ProtocolUnknownError):
        build_send_payload(real, conversation_id=None, model="x", prompt="y", stream=True)


def test_fingerprint_snapshot_detects_changes(tmp_path: Path):
    profile = load_profile(MOCK_PROFILE)
    snapshot = tmp_path / "fingerprint.json"
    assert compare_snapshot(profile, snapshot) == ["snapshot-missing"]

    write_snapshot(profile, snapshot)
    assert compare_snapshot(profile, snapshot) == []

    data = profile.model_dump()
    data["endpoints"]["send_message"]["path"] = "/api/changed"
    changed = Profile.model_validate(data)
    changes = compare_snapshot(changed, snapshot)
    assert any("endpoint send_message" in item for item in changes)
    assert any("fingerprint_hash changed" in item for item in changes)


def test_snapshot_file_is_valid_json(tmp_path: Path):
    profile = load_profile(MOCK_PROFILE)
    snapshot = write_snapshot(profile, tmp_path / "fp.json")
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["fingerprint_hash"] == profile.fingerprint_hash()
    assert payload["fingerprint"]["stream"]["transport"] == "sse"


def test_captured_profile_is_ready():
    """真实档案已由 captures/baseline 回填：关键项必须是 confirmed 且带证据。"""
    profile = load_profile(REAL_PROFILE)
    assert profile.status == "captured"
    assert profile.upstream.base_url.value == "https://api.hunyuan.tencent.com"
    assert profile.require_endpoint("send_message").path == "/api/new-portal/chat/{conversation_id}"
    assert profile.require_endpoint("create_conversation").path == "/api/new-portal/generate/id"
    assert profile.require_endpoint("stop_generation").path == "/api/new-portal/stop/chat/{conversation_id}"
    assert profile.require_endpoint("history").path == "/api/new-portal/chat/conversation/list"
    assert profile.require_endpoint("models").path == "/api/new-portal/getChatConfig"

    stream = profile.require_stream()
    assert (stream.transport, stream.parser, stream.done_marker) == ("sse", "sse", "[DONE]")
    assert stream.type_field == "$.type"
    assert stream.type_map["text"] == "text" and stream.type_map["thought"] == "reasoning"
    assert stream.event_map["text"] == ["$.msg"]

    assert profile.model_ids() == ["hy4-preview"]
    assert profile.agent_id == "HunyuanDefault"
    assert profile.auth.required_headers["chat_version"] == "v1"
    assert profile.auth.cookie_names == ["hunyuan_source", "hunyuan_token", "hunyuan_user"]
    assert profile.request_schema["status"] == "confirmed"
    # 本地适配：messages（system 人格 + 历史）由本代理渲染成 prompt 文本（见 app/upstream/chat.py）
    assert profile.request_schema["history_mode"] == "full_history"
    assert profile.request_schema["effort_options"]["values"] == ["no_think", "high"]
    body_template = profile.request_schema["body_template"]
    assert body_template["prompt"] == "{messages_text}"
    assert body_template["displayPrompt"] == "{messages_text}"
    assert body_template["effort"] == "{effort}"
