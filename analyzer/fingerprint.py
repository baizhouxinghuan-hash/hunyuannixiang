"""请求指纹（任务书 §6）。

指纹由以下要素构成::

    method + host + path + content-type + request body keys + response content-type

用途：
1. 把“同一类请求”归并（例如所有聊天发送请求都共享一个指纹）；
2. 用于差分分析时区分“固定字段”和“变量字段”；
3. 用于协议变化检测。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlparse


def parse_url(url: str) -> tuple[str, str, dict[str, str]]:
    """返回 (host, path, query dict)。"""
    parsed = urlparse(url or "")
    return parsed.netloc, parsed.path, dict(parse_qsl(parsed.query, keep_blank_values=True))


def header_get(headers: Mapping[str, Any] | None, name: str, default: str | None = None) -> str | None:
    if not headers:
        return default
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return value if isinstance(value, str) else str(value)
    return default


def content_type_of(headers: Mapping[str, Any] | None) -> str | None:
    raw = header_get(headers, "content-type")
    if not raw:
        return None
    return raw.split(";")[0].strip().lower()


def body_keys(body: Any) -> list[str]:
    """JSON 体的顶层键（非 JSON 返回空列表）。"""
    if isinstance(body, Mapping):
        return sorted(str(k) for k in body.keys())
    return []


def parse_json(text: Any) -> Any:
    if not isinstance(text, str) or not text.strip():
        return None
    stripped = text.strip()
    if stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def json_shape(value: Any, *, depth: int = 2, _level: int = 0) -> Any:
    """结构化 shape（键 + 类型），用于比较两个请求的 body 结构。"""
    if _level >= depth:
        return type(value).__name__
    if isinstance(value, Mapping):
        return {str(k): json_shape(v, depth=depth, _level=_level + 1) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [json_shape(value[0], depth=depth, _level=_level + 1)] if value else []
    return type(value).__name__


def fingerprint(record: Mapping[str, Any]) -> str:
    """稳定字符串指纹，例如::

        POST aistudio.tencent.com /api/xxx application/json {conversation_id,model,prompt} <- application/json
    """
    method = str(record.get("method") or "GET").upper()
    host, path, _ = parse_url(str(record.get("url") or ""))
    req_ct = content_type_of(record.get("request_headers")) or "-"
    keys = body_keys(record.get("request_body_json"))
    resp_ct = record.get("response_content_type") or content_type_of(record.get("response_headers")) or "-"
    keys_part = "{" + ",".join(keys) + "}" if keys else "-"
    return f"{method} {host} {path} {req_ct} {keys_part} <- {resp_ct}"


def fingerprint_hash(fp: str) -> str:
    return hashlib.sha256(fp.encode("utf-8")).hexdigest()[:12]


def fingerprint_struct(record: Mapping[str, Any]) -> dict[str, Any]:
    method = str(record.get("method") or "GET").upper()
    host, path, query = parse_url(str(record.get("url") or ""))
    return {
        "method": method,
        "host": host,
        "path": path,
        "query_keys": sorted(query.keys()),
        "content_type": content_type_of(record.get("request_headers")),
        "body_keys": body_keys(record.get("request_body_json")),
        "body_shape": json_shape(record.get("request_body_json")),
        "response_content_type": record.get("response_content_type")
        or content_type_of(record.get("response_headers")),
    }
