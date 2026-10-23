"""认证：会话加载与“是否已登录”探测（任务书 §11 / §31 / §32）。

安全边界
--------
* 只使用**用户自己**导出的 storage_state / cookie；
* 不实现任何验证码、登录绕过、第三方凭证窃取；
* 所有对外输出经过脱敏。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.protocol.profile import Profile
from app.upstream.errors import AIStudioAuthError, AIStudioError
from app.upstream.session import SessionState, load_session
from app.upstream.transport import UpstreamTransport


def build_session(config: AppConfig) -> SessionState:
    """按配置加载会话（缺失时抛 AIStudioAuthError，不静默降级）。"""
    cookie_text = None
    if config.auth.mode == "cookie_header":
        path = config.path(config.auth.cookie_header_file)
        if path.exists():
            cookie_text = path.read_text(encoding="utf-8").strip()
    return load_session(
        mode=config.auth.mode,
        storage_state_path=config.storage_state_path,
        cookie_header_text=cookie_text,
    )


def session_report(config: AppConfig) -> dict[str, Any]:
    """不抛异常的会话状态报告（供 /health 使用）。"""
    storage_state = Path(config.storage_state_path)
    report: dict[str, Any] = {
        "mode": config.auth.mode,
        "storage_state": str(storage_state),
        "storage_state_exists": storage_state.exists(),
    }
    try:
        session = build_session(config)
    except AIStudioAuthError as exc:
        report.update({"loaded": False, "reason": exc.message})
        return report
    report.update({"loaded": True, **session.summary()})
    return report


async def probe_login_state(transport: UpstreamTransport, profile: Profile) -> dict[str, Any]:
    """探测登录状态：优先用户信息接口，其次模型列表接口。"""
    result: dict[str, Any] = {"authenticated": None, "reason": "no-probe-endpoint-confirmed", "endpoint": None}

    for name in ("user_info", "models", "chat_config"):
        spec = profile.endpoints.get(name)
        if spec is None or not spec.known:
            continue
        try:
            resp = await transport.request(spec.method or "GET", spec.path or "")
        except AIStudioAuthError as exc:
            return {"authenticated": False, "reason": exc.message, "endpoint": spec.path}
        except AIStudioError as exc:
            return {"authenticated": False, "reason": exc.message, "endpoint": spec.path}
        body = resp.json()
        return {
            "authenticated": True,
            "reason": "upstream returned JSON from an authenticated endpoint",
            "endpoint": spec.path,
            "status_code": resp.status_code,
            "body_keys": sorted(body.keys()) if isinstance(body, dict) else None,
        }
    return result
