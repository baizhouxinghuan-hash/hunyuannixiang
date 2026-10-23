"""API 层公共依赖（本地 API Key 校验）。"""

from __future__ import annotations

from fastapi import Request

from app.upstream.errors import AIStudioRequestError


def verify_api_key(request: Request) -> None:
    """可选的本地 API Key 校验。

    * ``config.yaml: server.api_keys`` 为空 -> 不校验（默认；仅监听 127.0.0.1 时推荐）
    * 非空 -> 必须带 ``Authorization: Bearer <key>`` 或 ``X-API-Key: <key>``
    * Key 错误 -> **HTTP 401** + ``code=invalid_api_key``（与 OpenAI 行为一致）
    """
    keys = request.app.state.runtime.config.server.api_keys
    if not keys:
        return
    authorization = request.headers.get("authorization") or ""
    x_api_key = request.headers.get("x-api-key") or ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    else:
        token = x_api_key.strip()
    if token not in keys:
        raise AIStudioRequestError(
            "Incorrect API key provided. Set it in config.yaml (server.api_keys).",
            code="invalid_api_key",
            param="Authorization",
            status_code=401,
        )
