"""日志系统（任务书 §28）。

logs/
├── server.log    服务生命周期 / 请求摘要
├── request.log   每个 OpenAI 请求一行（含 request_id / 模型 / 会话 / 延迟）
├── upstream.log  上游调用（URL、状态、首 token 延迟，凭证一律脱敏）
└── error.log     仅 ERROR 及以上

所有 handler 同时挂 RedactingFilter 与 RedactingFormatter，双重脱敏。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from app.config import LoggingConfig
from app.security.redaction import RedactingFilter, RedactingFormatter, redact_obj

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_FILTER = RedactingFilter()
_configured = False


def _mk_handler(path: Path, level: int, formatter: logging.Formatter) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(_FILTER)
    return handler


def setup_logging(cfg: LoggingConfig | None = None, *, force: bool = False) -> None:
    """初始化日志。重复调用默认无害。"""
    global _configured
    if _configured and not force:
        return

    level = getattr(logging, str(getattr(cfg, "level", "INFO")).upper(), logging.INFO)
    log_dir = Path(getattr(cfg, "dir", "./logs"))
    formatter = RedactingFormatter(_FORMAT)
    json_formatter = RedactingFormatter("%(message)s")

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(min(level, logging.INFO))

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(_FILTER)
    root.addHandler(console)

    root.addHandler(_mk_handler(log_dir / "server.log", level, formatter))
    error_handler = _mk_handler(log_dir / "error.log", logging.ERROR, formatter)
    root.addHandler(error_handler)

    for name, filename, lvl in (
        ("aistudio.request", "request.log", logging.INFO),
        ("aistudio.upstream", "upstream.log", level),
    ):
        logger = logging.getLogger(name)
        logger.setLevel(lvl)
        logger.propagate = True
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.addHandler(_mk_handler(log_dir / filename, lvl, json_formatter))

    _configured = True


def get_request_logger() -> logging.Logger:
    return logging.getLogger("aistudio.request")


def get_upstream_logger() -> logging.Logger:
    return logging.getLogger("aistudio.upstream")


def log_json(logger: logging.Logger, level: int, payload: dict[str, Any]) -> None:
    """以单行 JSON 记录结构化日志，写入前强制脱敏。"""
    logger.log(level, json.dumps(redact_obj(payload), ensure_ascii=False, default=str))
