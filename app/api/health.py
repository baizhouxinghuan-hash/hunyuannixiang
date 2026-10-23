"""健康检查与协议状态（任务书 §32 / §37）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app import __version__
from app.upstream.auth import session_report
from app.upstream.upload import upload_status

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request, probe: bool = Query(True, description="是否向上游探测登录状态")) -> dict[str, Any]:
    runtime = request.app.state.runtime
    cfg = runtime.config

    payload: dict[str, Any] = {
        "status": "ok",
        "version": __version__,
        "upstream": "unknown",
        "authenticated": None,
        "session": session_report(cfg),
        "protocol": {
            "version": runtime.profile.protocol_version,
            "status": runtime.profile.status,
            "fingerprint": runtime.profile.fingerprint_hash(),
            "base_url": runtime.profile.upstream.base_url.value,
            "changes": runtime.protocol_changes,
        },
        "rate_limit": {
            "enabled": cfg.rate_limit.enabled,
            "max_concurrency": cfg.rate_limit.max_concurrency,
        },
        "conversations": runtime.client.conversations.stats(),
        "upload": upload_status(runtime.profile),
        "unknown_features": runtime.profile.unknown_features(),
    }

    if runtime.auth_error:
        payload["authenticated"] = False
        payload["auth_error"] = runtime.auth_error

    if probe and cfg.auth.mode != "none":
        result = await runtime.client.probe_login_state()
        payload["upstream"] = "reachable" if result.get("authenticated") is not None else "unknown"
        payload["authenticated"] = result.get("authenticated")
        payload["probe"] = result

    if runtime.auth_error:
        payload["status"] = "degraded"
    elif payload["unknown_features"]:
        # 有未确认的协议细节：服务可用，但相关功能会显式报 501
        payload["status"] = "ok_with_unknown_protocol"
    return payload


@router.get("/v1/protocol")
async def protocol(request: Request) -> dict[str, Any]:
    """协议档案摘要（排障用；不返回任何凭证）。"""
    runtime = request.app.state.runtime
    return runtime.profile.to_summary()
