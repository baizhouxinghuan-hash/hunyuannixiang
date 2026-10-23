"""uvicorn 启动入口。"""

from __future__ import annotations

import logging

import uvicorn

from app.config import AppConfig, get_config
from app.logging_setup import setup_logging
from app.main import create_app

log = logging.getLogger("aistudio.server")


def run(
    *,
    host: str | None = None,
    port: int | None = None,
    reload: bool = False,
    log_level: str | None = None,
    config: AppConfig | None = None,
) -> None:
    cfg = config or get_config()
    setup_logging(cfg.logging)
    host = host or cfg.server.host
    port = int(port or cfg.server.port)
    level = (log_level or cfg.logging.level).lower()

    log.info("启动服务 http://%s:%s/v1 （日志目录：%s）", host, port, cfg.log_dir)
    if reload:
        # reload 模式必须使用 import string
        uvicorn.run("app.main:create_app", factory=True, host=host, port=port, reload=True, log_level=level)
        return
    uvicorn.run(create_app(cfg), host=host, port=port, log_level=level)
