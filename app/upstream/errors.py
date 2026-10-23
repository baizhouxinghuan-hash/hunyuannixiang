"""上游错误体系（任务书 §25）。

映射关系
--------
HTTP / 业务错误  ->  AIStudio* 异常  ->  OpenAI 风格 error JSON

特别注意（证据 E9）：未登录时 ``/api/*`` 返回 **HTTP 200 + text/html**（SPA 回退页），
而不是 401 JSON，因此必须显式把“JSON 接口返回 HTML”判定为未登录。
"""

from __future__ import annotations

from typing import Any, Mapping

#: 任务书 §25 要求覆盖的状态码
COVERED_STATUS_CODES = (400, 401, 403, 404, 408, 409, 413, 422, 429, 500, 502, 503, 504)


class AIStudioError(Exception):
    """所有上游/适配层异常的基类。"""

    error_type = "upstream_error"
    default_message = "AI Studio upstream error"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        code: str | None = None,
        param: str | None = None,
        payload: Any = None,
        retryable: bool = False,
        hint: str | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.status_code = status_code
        self.code = code
        self.param = param
        self.payload = payload
        self.retryable = retryable
        self.hint = hint


class AIStudioAuthError(AIStudioError):
    """未登录 / 会话过期 / 403。"""

    error_type = "authentication_error"
    default_message = "AI Studio session is missing or expired"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 401)
        kwargs.setdefault(
            "hint",
            "重新执行 python tools/capture.py --interactive 完成登录并导出 storage_state，"
            "或运行 python tools/import_session.py 检查会话是否过期。",
        )
        super().__init__(message, **kwargs)


class AIStudioRateLimitError(AIStudioError):
    error_type = "rate_limit_error"
    default_message = "AI Studio rate limit reached"

    def __init__(self, message: str | None = None, *, retry_after: float | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 429)
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class AIStudioRequestError(AIStudioError):
    """上游 4xx（非认证、非限流）。"""

    error_type = "invalid_request_error"
    default_message = "AI Studio rejected the request"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 400)
        super().__init__(message, **kwargs)


class AIStudioStreamError(AIStudioError):
    """流式中断 / 解析失败。"""

    error_type = "stream_error"
    default_message = "AI Studio stream failed"

    def __init__(self, message: str | None = None, *, partial_text: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(message, **kwargs)
        self.partial_text = partial_text


class AIStudioModelError(AIStudioError):
    """模型不存在 / 不支持。"""

    error_type = "invalid_request_error"
    default_message = "Unknown or unsupported model"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 404)
        kwargs.setdefault("code", "model_not_found")
        super().__init__(message, **kwargs)


class AIStudioUpstreamError(AIStudioError):
    """5xx / 网络层失败。"""

    error_type = "upstream_error"
    default_message = "AI Studio upstream unavailable"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 502)
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class AIStudioTimeoutError(AIStudioUpstreamError):
    """等待上游生成超时（非流式请求超过 ``upstream.nonstream_timeout``）。

    上游只有 SSE，非流式必须等整段生成完才能返回；与其让客户端自己超时断连，
    不如明确返回 504 + code=upstream_timeout，并提示改用流式。
    """

    error_type = "server_error"
    default_message = "AI Studio upstream generation timed out"

    def __init__(self, message: str | None = None, *, timeout: float | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 504)
        kwargs.setdefault("code", "upstream_timeout")
        kwargs.setdefault(
            "hint",
            "非流式请求需要等上游整段生成完；请改用 stream:true，"
            "或调大 config.yaml 的 upstream.nonstream_timeout（当前值见该配置）。",
        )
        super().__init__(message, **kwargs)
        self.timeout = timeout


class ProtocolUnknownError(AIStudioError):
    """协议事实尚未由抓包确认（UNKNOWN）。

    这是本项目**刻意保留**的行为：宁可明确报错，也不猜测 endpoint / 字段名。
    """

    error_type = "upstream_protocol_unknown"
    default_message = "AI Studio protocol detail is not confirmed yet (UNKNOWN)"

    def __init__(self, feature: str, detail: str | None = None, **kwargs: Any) -> None:
        message = f"protocol detail not confirmed: {feature}"
        if detail:
            message = f"{message} — {detail}"
        kwargs.setdefault("status_code", 501)
        kwargs.setdefault("code", "protocol_unknown")
        kwargs.setdefault(
            "hint",
            "请运行 python tools/capture.py --interactive 抓取一次真实请求，"
            "然后回填 protocol/current/profile.yaml（详见 docs/protocol.md）。",
        )
        super().__init__(message, **kwargs)
        self.feature = feature


class ProtocolChangedError(AIStudioError):
    """上游协议发生变化（endpoint / schema 指纹不一致）。"""

    error_type = "upstream_protocol_changed"
    default_message = "AI Studio upstream protocol changed"

    def __init__(self, message: str | None = None, *, changes: list[str] | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 502)
        kwargs.setdefault("code", "protocol_changed")
        super().__init__(message, **kwargs)
        self.changes = changes or []


# ---------------------------------------------------------------------------
# HTTP 状态码 -> 异常
# ---------------------------------------------------------------------------
_STATUS_TO_EXC: dict[int, type[AIStudioError]] = {
    400: AIStudioRequestError,
    401: AIStudioAuthError,
    403: AIStudioAuthError,
    404: AIStudioRequestError,
    408: AIStudioUpstreamError,
    409: AIStudioRequestError,
    413: AIStudioRequestError,
    422: AIStudioRequestError,
    429: AIStudioRateLimitError,
    500: AIStudioUpstreamError,
    502: AIStudioUpstreamError,
    503: AIStudioUpstreamError,
    504: AIStudioUpstreamError,
}

_OPENAI_TYPE_BY_EXC: dict[type[AIStudioError], str] = {
    AIStudioAuthError: "invalid_request_error",
    AIStudioRequestError: "invalid_request_error",
    AIStudioModelError: "invalid_request_error",
    AIStudioRateLimitError: "rate_limit_error",
    AIStudioUpstreamError: "server_error",
    AIStudioTimeoutError: "server_error",
    AIStudioStreamError: "server_error",
    ProtocolUnknownError: "server_error",
    ProtocolChangedError: "server_error",
}


def is_html_response(content_type: str | None, body: str | bytes | None) -> bool:
    """判断响应是否为 HTML 回退页（HTML 不可能是接口 JSON）。

    常见于未登录、会话过期或网关错误页。注意：抓包确认真实接口在未登录时
    返回的是 JSON 错误 / 401，而不是 SPA HTML；这里只做“非 JSON”防御性判定
    （见 docs/findings.md 对 E9 的修正）。
    """
    ct = (content_type or "").lower()
    if "text/html" in ct:
        return True
    if isinstance(body, (bytes, bytearray)):
        sample = bytes(body)[:512].decode("utf-8", "ignore")
    elif isinstance(body, str):
        sample = body[:512]
    else:
        return False  # 已解析成 dict/list/None 的响应不可能是 HTML 页面
    lowered = sample.lower()
    return "<!doctype html" in lowered or "<html" in lowered


def extract_business_error(body: Any) -> tuple[str | None, str | None]:
    """从业务 JSON 中提取 (code, message)；无错误时返回 (None, None)。"""
    if not isinstance(body, Mapping):
        return None, None
    code = body.get("code")
    if code is None:
        code = body.get("retcode", body.get("error_code"))
    message = body.get("message")
    if message is None:
        message = body.get("msg", body.get("error_message", body.get("detail")))
    if isinstance(code, int) and code in (0, 200) and not body.get("error"):
        return None, None
    if isinstance(code, str) and code.strip().lower() in ("", "0", "ok", "success"):
        if not body.get("error"):
            return None, None
    if code is None and message is None and isinstance(body.get("error"), Mapping):
        err = body["error"]
        return str(err.get("code") or ""), str(err.get("message") or "")
    return (str(code) if code is not None else None), (str(message) if message is not None else None)


def error_from_response(
    *,
    status_code: int,
    headers: Mapping[str, Any] | None = None,
    body: Any = None,
    content_type: str | None = None,
    raw_text: str | None = None,
    url: str | None = None,
) -> AIStudioError:
    """把一次失败的 HTTP 响应转换成具体异常。"""
    headers = headers or {}
    where = f" ({url})" if url else ""

    if is_html_response(content_type, raw_text if raw_text is not None else body):
        return AIStudioAuthError(
            f"upstream returned an HTML page instead of JSON{where}; "
            "通常是未登录 / 会话已失效，或网关错误页（见 docs/findings.md 对 E9 的修正）"
        )

    if status_code >= 500:
        exc_cls: type[AIStudioError] = AIStudioUpstreamError
    else:
        exc_cls = _STATUS_TO_EXC.get(status_code, AIStudioRequestError)

    code, message = extract_business_error(body)
    if isinstance(raw_text, str) and raw_text:
        detail = message or raw_text[:400]
    else:
        detail = message or "upstream error"

    if exc_cls is AIStudioRateLimitError:
        retry_after_raw = headers.get("retry-after")
        if retry_after_raw is None:
            retry_after_raw = headers.get("Retry-After")
        try:
            retry_after = float(retry_after_raw) if retry_after_raw is not None else None
        except (TypeError, ValueError):
            retry_after = None
        return AIStudioRateLimitError(
            f"HTTP {status_code}{where}: {detail}",
            retry_after=retry_after,
            code=code or "rate_limited",
            payload=body,
        )

    return exc_cls(
        f"HTTP {status_code}{where}: {detail}",
        status_code=status_code if status_code >= 400 else None,
        code=code,
        payload=body,
    )


def is_normal_response(status_code: int, content_type: str | None) -> bool:
    """HTTP 层是否正常（2xx 且不是 HTML 回退页）。"""
    return 200 <= status_code < 300 and not is_html_response(content_type, None)


def to_openai_error(exc: BaseException) -> tuple[int, dict[str, Any]]:
    """把异常转换成 (HTTP 状态码, OpenAI 风格 error 体)。"""
    if not isinstance(exc, AIStudioError):
        return 500, {
            "error": {
                "message": f"internal error: {type(exc).__name__}",
                "type": "server_error",
                "param": None,
                "code": "internal_error",
            }
        }

    type_name = _OPENAI_TYPE_BY_EXC.get(type(exc), exc.error_type)
    if isinstance(exc, ProtocolUnknownError):
        status = 501
    elif isinstance(exc, ProtocolChangedError):
        status = 502
    else:
        status = exc.status_code or 500
        if status < 400:
            status = 500

    message = exc.message
    if exc.hint:
        message = f"{message} | hint: {exc.hint}"

    return status, {
        "error": {
            "message": message,
            "type": type_name,
            "param": exc.param,
            "code": exc.code or exc.error_type,
        }
    }

