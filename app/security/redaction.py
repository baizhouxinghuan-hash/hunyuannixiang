"""全局脱敏工具（任务书 §5 / §28 / §41 H）。

设计目标
--------
1. 凭证出现在日志、抓包产物、异常信息里时，必须自动变成 ``****``；
2. 同一段文本反复脱敏结果稳定（幂等），便于断言与比较；
3. 对任意嵌套结构（dict / list / str / bytes）递归生效。

本模块是唯一的脱敏入口，任何“写日志 / 写文件”的地方都必须经过它。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Mapping, Sequence

MASK = "****"

#: 命中即整体打码的键名（大小写不敏感，按精确名比较）
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "cookies",
        "set-cookie",
        "set_cookie",
        "cookie_header",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "session",
        "sessionid",
        "session_id",
        "jsessionid",
        "sid",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "pwd",
        "signature",
        "sign",
        "nonce",
        "csrf",
        "csrf_token",
        "x-csrf-token",
        "apikey",
        "api_key",
        "tmpsecretid",
        "tmpsecretkey",
        "secretid",
        "secretkey",
    }
)

#: 键名里只要包含这些片段（且值看起来像凭证）也打码
SENSITIVE_KEY_PARTS: tuple[str, ...] = ("token", "secret", "password", "credential", "signature")

_HEADER_LINE_RE = re.compile(
    r"(?im)^(\s*[-*]?\s*(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*)([^\r\n%]{6,})$"
)
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(bearer|basic|digest)\s+[A-Za-z0-9\-._~+/=]{6,}")
_KEYS_ALT = "|".join(sorted((re.escape(k) for k in SENSITIVE_KEYS), key=len, reverse=True))
_KV_RE = re.compile(
    r"(?i)\b(" + _KEYS_ALT + r')\b(["\']?\s*[:=]\s*["\']?)([^"\'\s,;&}{\]\)]{4,})'
)
#: 模糊匹配：键名里**包含** session/token/secret/... 的 KV 对（处理 session_key、csrf_token 等）
_KV_FUZZY_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_\-]*(?:session|token|secret|password|passwd|sign|signature|nonce|credential)"
    r"[A-Za-z0-9_\-]*)\s*([:=])\s*([^\s,;&\"'}\)\]]{4,})"
)
#: 「长且像凭证」的裸值（例如 hex/base64 串），仅在明确上下文里使用
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")


def redact_text(value: str) -> str:
    """对文本中的凭证做脱敏，保持幂等。"""
    if not value:
        return value
    out = _HEADER_LINE_RE.sub(lambda m: m.group(1) + MASK, value)
    out = _AUTH_SCHEME_RE.sub(lambda m: m.group(1) + " " + MASK, out)
    out = _KV_RE.sub(lambda m: m.group(1) + m.group(2) + MASK, out)
    out = _KV_FUZZY_RE.sub(lambda m: m.group(1) + m.group(2) + MASK, out)
    # JWT 形态（三段式）基本等价于凭证，直接打码
    out = _LONG_TOKEN_RE.sub(MASK, out)
    return out


def _is_sensitive_key(key: Any) -> bool:
    name = str(key).strip().lower()
    if name in SENSITIVE_KEYS:
        return True
    if name.startswith("x-") and name[2:] in SENSITIVE_KEYS:
        return True
    return any(part in name for part in SENSITIVE_KEY_PARTS)


def redact_obj(obj: Any) -> Any:
    """递归脱敏任意结构。"""
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, (bytes, bytearray)):
        try:
            return redact_text(bytes(obj).decode("utf-8", "replace")).encode("utf-8")
        except Exception:  # pragma: no cover - 理论上不会走到
            return MASK.encode("utf-8")
    if isinstance(obj, Mapping):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            out[k] = MASK if _is_sensitive_key(k) else redact_obj(v)
        return out
    if isinstance(obj, (list, tuple, set)):
        redacted = [redact_obj(v) for v in obj]
        return type(obj)(redacted) if isinstance(obj, (list, tuple)) else redacted
    if hasattr(obj, "model_dump"):  # pydantic
        try:
            return redact_obj(obj.model_dump())
        except Exception:  # pragma: no cover
            return MASK
    return redact_text(str(obj))


def redact_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """对 HTTP 头做脱敏，保留可读的键名。"""
    if not headers:
        return {}
    return {str(k): (MASK if _is_sensitive_key(k) else redact_text(str(v))) for k, v in headers.items()}


def scan_for_secrets(text: str, secrets: Iterable[str], *, min_length: int = 6) -> list[str]:
    """返回 ``text`` 中出现过的凭证值（仅返回出现次数信息，不返回原值）。

    用于测试断言“抓包产物里不含原始 cookie 值”，以及发布前自检。
    """
    hits: list[str] = []
    if not text:
        return hits
    for secret in secrets:
        if not secret or len(secret) < min_length:
            continue
        if secret in text:
            hits.append(f"<secret#{len(hits) + 1}:len={len(secret)}>")
    return hits


class RedactingFilter(logging.Filter):
    """logging 过滤器：在格式化前脱敏 ``msg`` 与 ``args``。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        try:
            if record.args:
                # 有位置参数时 msg 是格式串（可能含 %s 占位符），不能直接套用脱敏正则
                record.args = redact_obj(record.args)
                if not isinstance(record.msg, str) or "%" not in record.msg:
                    record.msg = redact_obj(record.msg)
            elif isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            else:
                record.msg = redact_obj(record.msg)
        except Exception:  # pragma: no cover - 脱敏绝不能因异常中断日志
            pass
        return True


class RedactingFormatter(logging.Formatter):
    """logging 格式化器：格式化后的整行再做一次脱敏（兜底）。"""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D102
        return redact_text(super().format(record))
