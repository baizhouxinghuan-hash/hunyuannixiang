"""FastAPI 应用装配（任务书 §32 / §38）。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import chat as chat_api
from app.api import health as health_api
from app.api import models as models_api
from app.config import AppConfig, get_config
from app.logging_setup import setup_logging
from app.protocol.profile import Profile, compare_snapshot, load_profile, write_snapshot
from app.upstream.auth import build_session
from app.upstream.client import AIStudioClient
from app.upstream.errors import (
    AIStudioAuthError,
    AIStudioError,
    ProtocolChangedError,
    to_openai_error,
)

log = logging.getLogger("aistudio.server")


@dataclass
class Runtime:
    """进程级运行时依赖。"""

    config: AppConfig
    profile: Profile
    client: AIStudioClient
    semaphore: asyncio.Semaphore | None = None
    auth_error: str | None = None
    protocol_changes: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, config: AppConfig | None = None, *, profile_path: str | None = None) -> "Runtime":
        cfg = config or get_config()
        profile = load_profile(profile_path or cfg.profile_path)

        session = None
        auth_error: str | None = None
        if cfg.auth.mode != "none":
            try:
                session = build_session(cfg)
            except AIStudioAuthError as exc:
                auth_error = exc.message

        client = AIStudioClient(cfg, profile, session=session)
        semaphore = (
            asyncio.Semaphore(max(1, cfg.rate_limit.max_concurrency))
            if cfg.rate_limit.enabled
            else None
        )
        return cls(config=cfg, profile=profile, client=client, semaphore=semaphore, auth_error=auth_error)

    async def aclose(self) -> None:
        await self.client.aclose()

    def check_protocol(self) -> list[str]:
        """与快照比对协议指纹（任务书 §37 / §43）。"""
        changes = compare_snapshot(self.profile, self.config.snapshot_path)
        self.protocol_changes = changes
        if changes == ["snapshot-missing"]:
            write_snapshot(self.profile, self.config.snapshot_path)
            log.info("已写出协议指纹快照: %s", self.config.snapshot_path)
            return []
        if changes and self.config.protocol.warn_on_change:
            log.warning("WARNING: upstream protocol changed -> %s", "; ".join(changes))
        return changes


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or get_config()
    setup_logging(cfg.logging)
    runtime = Runtime.build(cfg)
    changes = runtime.check_protocol()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """进程退出时关闭上游 httpx 客户端（否则 keep-alive 连接会拖到进程被强杀）。"""
        try:
            yield
        finally:
            await runtime.aclose()

    app = FastAPI(
        title="AI Studio OpenAI-Compatible Proxy",
        version=__version__,
        description=(
            "把腾讯混元 AI Studio 网页端会话封装成 OpenAI 兼容 API。"
            "协议事实全部来自 protocol/current/profile.yaml，未确认项会显式报错。"
        ),
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    # CORS 规范：``allow_credentials=True`` 时浏览器拒绝 ``Access-Control-Allow-Origin: *``，
    # 因此通配源下必须关掉凭证；需要带 Cookie 的跨域调用请把具体源写进 server.cors_origins。
    allow_all_origins = "*" in cfg.server.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.server.cors_origins,
        allow_credentials=not allow_all_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(AIStudioError)
    async def _ai_studio_error_handler(_: Request, exc: AIStudioError) -> JSONResponse:
        status, body = to_openai_error(exc)
        return JSONResponse(status_code=status, content=body)

    @app.exception_handler(ProtocolChangedError)
    async def _protocol_changed_handler(_: Request, exc: ProtocolChangedError) -> JSONResponse:
        status, body = to_openai_error(exc)
        return JSONResponse(status_code=status, content=body)

    app.include_router(health_api.router)
    app.include_router(models_api.router)
    app.include_router(chat_api.router)

    if runtime.auth_error:
        log.warning("未加载到登录会话：%s（/v1/chat/completions 会返回 401）", runtime.auth_error)
    if changes:
        log.warning("协议指纹变化：%s", changes)
    return app


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


__all__ = ["Runtime", "create_app", "get_runtime"]
