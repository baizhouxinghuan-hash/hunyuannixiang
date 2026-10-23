"""模型映射测试（任务书 §20）。"""

from __future__ import annotations

from app.protocol.profile import load_profile
from app.upstream.models import ModelInfo, default_model_name, list_models
from tests.conftest import MOCK_PROFILE, REAL_PROFILE, UNKNOWN_PROFILE


def test_list_models_from_profile():
    profile = load_profile(MOCK_PROFILE)
    models = list_models(profile)
    assert [model.id for model in models] == ["mock-chat"]
    assert models[0].upstream == "mock-chat-v1"
    assert models[0].status == "confirmed"
    payload = models[0].to_openai()
    assert payload["object"] == "model"
    assert payload["x_upstream_model"] == "mock-chat-v1"


def test_override_wins_over_profile_mapping():
    profile = load_profile(MOCK_PROFILE)
    models = list_models(profile, override={"custom": "mock-chat-v1"})
    mapping = {model.id: model.upstream for model in models}
    assert mapping == {"mock-chat": "mock-chat-v1", "custom": "mock-chat-v1"}


def test_real_profile_has_no_models_yet():
    """未抓包确认前不返回任何模型（而不是编造模型名）。"""
    profile = load_profile(UNKNOWN_PROFILE)
    assert list_models(profile) == []
    assert profile.model_ids() == []


def test_default_model_name_priority():
    profile = load_profile(MOCK_PROFILE)
    assert default_model_name(profile) == "mock-chat"
    assert default_model_name(profile, configured="from-config") == "from-config"
    assert default_model_name(load_profile(UNKNOWN_PROFILE)) == ""


def test_model_info_openai_shape():
    payload = ModelInfo(id="a", upstream="b").to_openai()
    assert set(payload) >= {"id", "object", "created", "owned_by", "x_upstream_model", "x_status"}


def test_captured_profile_lists_hy4_preview():
    """抓包确认后的模型：hy4-preview（来自 conversation/list 与 getChatConfig）。"""
    models = list_models(load_profile(REAL_PROFILE))
    assert [m.id for m in models] == ["hy4-preview"]
    assert models[0].status == "confirmed"
