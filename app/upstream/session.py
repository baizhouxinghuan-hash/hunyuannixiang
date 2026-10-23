"""会话状态：Playwright storage_state / Cookie 头的加载、校验与脱敏摘要。

安全约定
--------
* 原始凭证只允许存在于 ``secrets/`` 目录；
* ``summary()`` 的输出永远是脱敏的，可以直接进日志或给用户看；
* 任何凭证缺失/过期都抛 :class:`AIStudioAuthError`，而不是静默继续。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from app.security.redaction import redact_obj
from app.upstream.errors import AIStudioAuthError


@dataclass
class SessionCookie:
    name: str
    value: str = field(repr=False, default="")
    domain: str = ""
    path: str = "/"
    expires: float | None = None
    secure: bool = False
    http_only: bool = False

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires in (None, 0, -1):
            return False  # session cookie：无法从存储判断，交给上游 401/HTML 暴露
        return float(self.expires) < (now if now is not None else time.time())

    def matches_host(self, host: str) -> bool:
        if not self.domain:
            return True
        domain = self.domain.lstrip(".").lower()
        host = host.lower()
        return host == domain or host.endswith("." + domain)


@dataclass
class SessionState:
    """一组可注入 HTTP 客户端的凭证。"""

    cookies: list[SessionCookie] = field(default_factory=list)
    origins: list[dict[str, Any]] = field(default_factory=list)
    source: str = "unknown"
    loaded_at: float = field(default_factory=time.time)

    # -- 构造 -------------------------------------------------------------
    @classmethod
    def empty(cls) -> "SessionState":
        return cls(cookies=[], origins=[], source="empty")

    @classmethod
    def from_storage_state(cls, path: str | Path) -> "SessionState":
        p = Path(path)
        if not p.exists():
            raise AIStudioAuthError(
                f"storage_state 不存在: {p}",
                hint="先执行 python tools/capture.py --interactive 完成登录并导出会话。",
            )
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AIStudioAuthError(f"storage_state 解析失败: {exc}") from exc

        cookies = []
        for raw in data.get("cookies", []) or []:
            if not isinstance(raw, Mapping) or not raw.get("name"):
                continue
            cookies.append(
                SessionCookie(
                    name=str(raw.get("name")),
                    value=str(raw.get("value", "")),
                    domain=str(raw.get("domain", "")),
                    path=str(raw.get("path", "/")),
                    expires=raw.get("expires"),
                    secure=bool(raw.get("secure", False)),
                    http_only=bool(raw.get("httpOnly", False)),
                )
            )
        return cls(cookies=cookies, origins=list(data.get("origins", []) or []), source=str(p))

    @classmethod
    def from_cookie_header(cls, text: str, *, source: str = "cookie_header") -> "SessionState":
        cookies: list[SessionCookie] = []
        for part in (text or "").split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, _, value = part.partition("=")
            cookies.append(SessionCookie(name=name.strip(), value=value.strip()))
        if not cookies:
            raise AIStudioAuthError("cookie_header 为空或格式不正确（应形如 a=1; b=2）")
        return cls(cookies=cookies, source=source)

    # -- 查询 -------------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        return not self.cookies

    def expired(self, now: float | None = None) -> list[SessionCookie]:
        return [c for c in self.cookies if c.is_expired(now)]

    def cookie_header(
        self, host: str | None = None, *, include_expired: bool = False, now: float | None = None
    ) -> str:
        chunks: list[str] = []
        for cookie in self.cookies:
            if host and not cookie.matches_host(host):
                continue
            if not include_expired and cookie.is_expired(now):
                continue
            chunks.append(f"{cookie.name}={cookie.value}")
        return "; ".join(chunks)

    def names(self) -> list[str]:
        return [c.name for c in self.cookies]

    def summary(self) -> dict[str, Any]:
        """脱敏摘要（可安全打印/写日志）。"""
        now = time.time()
        return redact_obj(
            {
                "source": self.source,
                "cookie_count": len(self.cookies),
                "cookie_names": sorted({c.name for c in self.cookies}),
                "domains": sorted({c.domain for c in self.cookies if c.domain}),
                "expired_cookies": sorted({c.name for c in self.expired(now)}),
                "origins": [o.get("origin") for o in self.origins if isinstance(o, Mapping)],
                "loaded_at": self.loaded_at,
            }
        )

    def redacted_dump(self) -> dict[str, Any]:
        """保留结构、值全部打码，用于排障。"""
        return redact_obj(
            {
                "source": self.source,
                "cookies": [
                    {
                        "name": c.name,
                        "value": c.value,
                        "domain": c.domain,
                        "path": c.path,
                        "expires": c.expires,
                        "secure": c.secure,
                        "httpOnly": c.http_only,
                    }
                    for c in self.cookies
                ],
                "origins": self.origins,
            }
        )

    # -- 持久化（只写 secrets/，由 .gitignore 排除）------------------------
    def save_storage_state(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cookies": [
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path,
                    "expires": c.expires if c.expires is not None else -1,
                    "httpOnly": c.http_only,
                    "secure": c.secure,
                    "sameSite": "Lax",
                }
                for c in self.cookies
            ],
            "origins": self.origins,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return p


def load_session(
    *,
    mode: str,
    storage_state_path: str | Path,
    cookie_header_text: str | None = None,
) -> SessionState:
    """按 config.auth.mode 加载会话。"""
    if mode == "none":
        return SessionState.empty()
    if mode == "cookie_header":
        if not cookie_header_text:
            raise AIStudioAuthError("config.auth.mode=cookie_header 但未提供 cookie 内容")
        return SessionState.from_cookie_header(cookie_header_text)
    return SessionState.from_storage_state(storage_state_path)

