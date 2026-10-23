"""会话/认证测试（任务书 §11 / §31）。"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.protocol.profile import load_profile
from app.security.redaction import MASK
from app.upstream.auth import probe_login_state
from app.upstream.errors import AIStudioAuthError
from app.upstream.session import SessionCookie, SessionState
from app.upstream.transport import UpstreamTransport
from tests.conftest import MOCK_PROFILE, run
from tests.fixtures.mock_upstream import create_mock_app

SAMPLE_COOKIE_VALUE = "SECRET-COOKIE-VALUE-123"


def write_storage_state(path, *, expired: bool = False) -> None:
    expires = time.time() - 3600 if expired else time.time() + 86400
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "session_key",
                        "value": SAMPLE_COOKIE_VALUE,
                        "domain": ".tencent.com",
                        "path": "/",
                        "expires": expires,
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax",
                    },
                    {"name": "uid", "value": "10001", "domain": "aistudio.tencent.com", "path": "/"},
                ],
                "origins": [{"origin": "https://aistudio.tencent.com", "localStorage": []}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_session_from_storage_state(tmp_path):
    path = tmp_path / "storage_state.json"
    write_storage_state(path)
    session = SessionState.from_storage_state(path)
    assert session.names() == ["session_key", "uid"]
    assert session.expired() == []
    # 域名匹配：.tencent.com 适用于 aistudio.tencent.com；uid 只发给同域
    header = session.cookie_header("aistudio.tencent.com")
    assert "uid=10001" in header
    assert "session_key=" in header
    assert session.cookie_header("other.example.com") == "uid=10001" or "session_key" not in session.cookie_header("other.example.com")


def test_missing_storage_state_raises_auth_error(tmp_path):
    with pytest.raises(AIStudioAuthError) as excinfo:
        SessionState.from_storage_state(tmp_path / "nope.json")
    assert "storage_state" in excinfo.value.message
    assert excinfo.value.hint


def test_expired_cookie_is_excluded_from_header(tmp_path):
    path = tmp_path / "storage_state.json"
    write_storage_state(path, expired=True)
    session = SessionState.from_storage_state(path)
    assert [cookie.name for cookie in session.expired()] == ["session_key"]
    assert "session_key" not in session.cookie_header("aistudio.tencent.com")


def test_cookie_header_parsing():
    session = SessionState.from_cookie_header("a=1; b=2; c=3")
    assert session.names() == ["a", "b", "c"]
    with pytest.raises(AIStudioAuthError):
        SessionState.from_cookie_header("")


def test_session_summary_and_dump_are_redacted(tmp_path):
    path = tmp_path / "storage_state.json"
    write_storage_state(path)
    session = SessionState.from_storage_state(path)
    blob = json.dumps(session.summary(), ensure_ascii=False) + json.dumps(session.redacted_dump(), ensure_ascii=False)
    assert SAMPLE_COOKIE_VALUE not in blob
    assert MASK in blob
    assert "session_key" in blob  # 名字保留，值打码


def test_probe_login_state_authenticated(config_factory, mock_upstream_app):
    cfg = config_factory()
    profile = load_profile(MOCK_PROFILE)
    session = SessionState.from_cookie_header("mock_session=mock-value")
    transport = UpstreamTransport(cfg, profile, session, http_transport=httpx.ASGITransport(app=mock_upstream_app))
    result = run(probe_login_state(transport, profile))
    assert result["authenticated"] is True
    assert result["endpoint"] == "/api/new-portal/getuserinfo"


def test_probe_login_state_detects_html_fallback(config_factory):
    """上游返回 HTML（非 JSON）-> 必须判定为未认证（防御性判定，见 E9 修正）。"""
    cfg = config_factory()
    profile = load_profile(MOCK_PROFILE)
    guard_app = create_mock_app(require_cookie="mock_session=correct")
    session = SessionState.from_cookie_header("mock_session=wrong")
    transport = UpstreamTransport(cfg, profile, session, http_transport=httpx.ASGITransport(app=guard_app))
    result = run(probe_login_state(transport, profile))
    assert result["authenticated"] is False
    assert "HTML" in result["reason"] or "SPA" in result["reason"]


def test_session_cookie_matches_host():
    cookie = SessionCookie(name="a", value="b", domain=".tencent.com")
    assert cookie.matches_host("aistudio.tencent.com")
    assert not cookie.matches_host("example.com")
    assert SessionCookie(name="a", value="b").matches_host("anything")
