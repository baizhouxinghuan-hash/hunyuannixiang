"""脱敏与安全产物测试（任务书 §5 / §28 / §41 H）。"""

from __future__ import annotations

import json
import logging

from app.security.redaction import (
    MASK,
    RedactingFilter,
    RedactingFormatter,
    redact_headers,
    redact_obj,
    redact_text,
    scan_for_secrets,
)
from tests.fixtures.synthetic import (
    SENSITIVE_COOKIE,
    SENSITIVE_TOKEN,
    build_synthetic_dicts,
)

SECRET_VALUES = (
    "SECRET-COOKIE-VALUE-123",
    "SECRET-TOKEN-VALUE-abcdef",
    "SECRET-TMP-ID",
    "SECRET-TMP-KEY",
)


def test_redact_text_masks_auth_header_line():
    text = "Authorization: Bearer abcdef123456\nX-Other: ok"
    assert "abcdef123456" not in redact_text(text)
    assert MASK in redact_text(text)
    assert "X-Other: ok" in redact_text(text)


def test_redact_text_masks_bare_kv_and_fuzzy_names():
    for raw in (
        "token=abcdef123456",
        "access_token: abcdef123456",
        "session_key=SECRET-COOKIE-VALUE-123",
        "csrf_token=zzzzzzzzzzzz",
        "signature=deadbeefdeadbeef",
    ):
        masked = redact_text(raw)
        assert "****" in masked, raw
        assert "abcdef123456" not in masked
        assert "zzzzzzzzzzzz" not in masked
        assert "deadbeefdeadbeef" not in masked


def test_redact_text_is_idempotent():
    text = "Cookie: a=b; c=d\nAuthorization: Bearer xyz1234567890"
    once = redact_text(text)
    assert redact_text(once) == once


def test_redact_obj_masks_sensitive_keys_recursively():
    payload = {
        "headers": {"cookie": SENSITIVE_COOKIE, "authorization": SENSITIVE_TOKEN},
        "cookies": [{"name": "session_key", "value": "SECRET-COOKIE-VALUE-123"}],
        "nested": {"list": [{"token": "abcdef123456"}]},
        "safe": "hello",
    }
    redacted = redact_obj(payload)
    serialized = json.dumps(redacted, ensure_ascii=False)
    for secret in SECRET_VALUES:
        assert secret not in serialized
    assert redacted["headers"]["cookie"] == MASK
    assert redacted["safe"] == "hello"


def test_redact_headers_keeps_names():
    redacted = redact_headers({"Cookie": "a=b", "Content-Type": "application/json"})
    assert redacted["Cookie"] == MASK
    assert redacted["Content-Type"] == "application/json"


def test_logging_filter_and_formatter_mask_values():
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="cookie=%s", args=(SENSITIVE_COOKIE,), exc_info=None,
    )
    RedactingFilter().filter(record)
    formatted = RedactingFormatter("%(message)s").format(record)
    assert "SECRET-COOKIE-VALUE-123" not in formatted
    assert MASK in formatted


def test_synthetic_capture_artifacts_contain_no_secrets():
    dicts = build_synthetic_dicts()
    blob = json.dumps(dicts, ensure_ascii=False)
    assert scan_for_secrets(blob, SECRET_VALUES) == []
    assert MASK in blob


def test_scan_for_secrets_detects_leaks():
    assert scan_for_secrets(f"cookie is {SENSITIVE_COOKIE}", SECRET_VALUES) != []
