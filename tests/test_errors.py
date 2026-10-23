"""错误映射测试（任务书 §25）。"""

from __future__ import annotations

import pytest

from app.upstream.errors import (
    COVERED_STATUS_CODES,
    AIStudioAuthError,
    AIStudioError,
    AIStudioModelError,
    AIStudioRateLimitError,
    AIStudioRequestError,
    AIStudioStreamError,
    AIStudioUpstreamError,
    ProtocolChangedError,
    ProtocolUnknownError,
    error_from_response,
    extract_business_error,
    is_html_response,
    to_openai_error,
)

SPA_HTML = "<!doctype html><html><head><title>Hy AI Studio</title></head></html>"


def test_required_status_codes_are_covered():
    for code in (400, 401, 403, 404, 408, 409, 413, 422, 429, 500, 502, 503, 504):
        assert code in COVERED_STATUS_CODES
        error = error_from_response(status_code=code, body={"code": 1, "message": "x"})
        assert isinstance(error, AIStudioError)


def test_html_response_means_not_authenticated():
    assert is_html_response("text/html; charset=utf-8", SPA_HTML)
    assert is_html_response(None, SPA_HTML)
    assert not is_html_response("application/json", '{"a": 1}')

    error = error_from_response(status_code=200, content_type="text/html", raw_text=SPA_HTML)
    assert isinstance(error, AIStudioAuthError)
    assert error.status_code == 401
    assert "E9" in error.message


def test_status_to_exception_mapping():
    assert isinstance(error_from_response(status_code=401, body={"message": "no"}), AIStudioAuthError)
    assert isinstance(error_from_response(status_code=403, body={"message": "no"}), AIStudioAuthError)
    assert isinstance(error_from_response(status_code=429, headers={"retry-after": "5"}), AIStudioRateLimitError)
    assert isinstance(error_from_response(status_code=503, body={"message": "down"}), AIStudioUpstreamError)
    assert isinstance(error_from_response(status_code=422, body={"message": "bad"}), AIStudioRequestError)


def test_retry_after_is_parsed():
    error = error_from_response(status_code=429, headers={"Retry-After": "2.5"})
    assert isinstance(error, AIStudioRateLimitError)
    assert error.retry_after == 2.5


def test_business_error_extraction():
    assert extract_business_error({"code": 0, "message": "ok"}) == (None, None)
    assert extract_business_error({"code": 1001, "message": "need login"}) == ("1001", "need login")
    assert extract_business_error({"retcode": -1, "msg": "boom"}) == ("-1", "boom")
    assert extract_business_error({"error": {"code": "x", "message": "y"}}) == ("x", "y")
    assert extract_business_error("not a dict") == (None, None)


def test_to_openai_error_shapes():
    status, body = to_openai_error(AIStudioAuthError("expired"))
    assert status == 401
    assert body["error"]["type"] == "invalid_request_error"
    assert "hint" in body["error"]["message"]

    status, body = to_openai_error(AIStudioRateLimitError("slow down", code="rate_limited"))
    assert status == 429
    assert body["error"]["type"] == "rate_limit_error"

    status, body = to_openai_error(AIStudioUpstreamError("bad gateway"))
    assert status == 502
    assert body["error"]["type"] == "server_error"

    status, body = to_openai_error(AIStudioModelError("nope"))
    assert status == 404
    assert body["error"]["code"] == "model_not_found"

    status, body = to_openai_error(ProtocolUnknownError("endpoint:send_message"))
    assert status == 501
    assert body["error"]["code"] == "protocol_unknown"

    status, body = to_openai_error(ProtocolChangedError("changed", changes=["endpoint x"]))
    assert status == 502
    assert body["error"]["code"] == "protocol_changed"

    status, body = to_openai_error(ValueError("plain"))
    assert status == 500
    assert body["error"]["code"] == "internal_error"


def test_stream_error_keeps_partial_text():
    error = AIStudioStreamError("broken", partial_text="half")
    assert error.partial_text == "half"
    assert error.error_type == "stream_error"


def test_protocol_unknown_error_carries_feature():
    error = ProtocolUnknownError("stream", "transport unknown")
    assert error.feature == "stream"
    assert "transport unknown" in error.message
    assert "protocol_unknown" == error.code
