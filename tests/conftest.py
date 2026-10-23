"""pytest 公共 fixture。

约定：测试全部是**同步函数**，内部用 ``run(coro)`` = ``asyncio.run`` 驱动，
因此不依赖 pytest-asyncio（本机未安装）。
"""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AppConfig  # noqa: E402
from app.main import create_app  # noqa: E402
from app.upstream.client import AIStudioClient  # noqa: E402
from app.upstream.session import SessionState  # noqa: E402
from app.upstream.transport import UpstreamTransport  # noqa: E402
from tests.fixtures.mock_upstream import create_mock_app  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
MOCK_PROFILE = FIXTURES_DIR / "protocol_mock.yaml"
#: mock 上游的“真实协议形状”档案：发送消息 path 带 {conversation_id}，
#: 没有会话时先调 /api/new-portal/generate/id 新建（用于验证“每次新会话”）。
MOCK_PATH_PROFILE = FIXTURES_DIR / "protocol_mock_path.yaml"
REAL_PROFILE = ROOT / "protocol" / "current" / "profile.yaml"
#: 模拟“尚未抓包确认”的档案（用于断言“未确认必须报错”）
UNKNOWN_PROFILE = FIXTURES_DIR / "protocol_unknown.yaml"


def run(coro):
    """在同步测试里跑协程。"""
    return asyncio.run(coro)


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


@pytest.fixture(scope="session")
def mock_sse_profile(tmp_project: Path) -> Path:
    """mock 档案的“真实形状”变体。

    真实档案就是这样：上游**只有 SSE**（`response_schema: unknown`，非流式由本地聚合），
    且 system/历史由本代理渲染成 prompt 文本（`history_mode: full_history` +
    `prompt: "{messages_text}"`）。
    """
    import yaml

    data = yaml.safe_load(MOCK_PROFILE.read_text(encoding="utf-8"))
    data["response_schema"] = {"status": "unknown", "notes": "test: 模拟上游只有 SSE"}
    schema = data["request_schema"]
    schema["history_mode"] = "full_history"
    schema["body_template"]["prompt"] = "{messages_text}"
    path = tmp_project / "protocol_mock_sse.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def tmp_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("aistudio-proxy")


@pytest.fixture
def config_factory(tmp_project: Path) -> Callable[..., AppConfig]:
    def make(**overrides: Any) -> AppConfig:
        profile = overrides.pop("profile", MOCK_PROFILE)
        data: dict[str, Any] = {
            "server": {"host": "127.0.0.1", "port": 1010, "api_keys": [], "cors_origins": ["*"]},
            "upstream": {"base_url": "http://mock-upstream", "transport": "httpx", "browser_headers": False},
            # 注意：storage_state 一律指向 tmp 目录，
            # 避免任何测试把假会话写进项目的 secrets/（历史上污染过一次）。
            "auth": {
                "mode": "none",
                "storage_state": str(tmp_project / "secrets" / "storage_state.json"),
                "browser_profile_dir": str(tmp_project / "secrets" / "browser_profile"),
                "cookie_header_file": str(tmp_project / "secrets" / "cookie_header.txt"),
            },
            "model": {"default": "", "mapping": {}},
            "conversation": {"mode": "auto", "cache_size": 16, "ttl_seconds": 600},
            "reasoning": {"mode": "passthrough"},
            "protocol": {
                "profile": str(profile),
                "snapshot": str(tmp_project / "schema_fingerprint.json"),
                "warn_on_change": True,
            },
            "logging": {"level": "WARNING", "dir": str(tmp_project / "logs")},
        }
        _deep_merge(data, copy.deepcopy(overrides))
        return AppConfig.model_validate(data)

    return make


@pytest.fixture
def mock_upstream_app() -> FastAPI:
    return create_mock_app()


@pytest.fixture
def make_app(config_factory, mock_upstream_app: FastAPI) -> Callable[..., FastAPI]:
    """构造我们的服务，并把上游指向 mock（通过 ASGITransport 注入，零网络）。"""

    def build(*, client_transport: Any = None, mock_app: FastAPI | None = None, **overrides: Any) -> FastAPI:
        cfg = config_factory(**overrides)
        app = create_app(cfg)
        runtime = app.state.runtime
        upstream_app = mock_app or mock_upstream_app
        transport = UpstreamTransport(
            cfg,
            runtime.profile,
            SessionState.from_cookie_header("mock_session=mock-value"),
            http_transport=client_transport or httpx.ASGITransport(app=upstream_app),
        )
        runtime.client = AIStudioClient(cfg, runtime.profile, transport=transport)
        return app

    return build


def local_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local-proxy")


def upstream_client(app: FastAPI) -> httpx.AsyncClient:
    """用于直接调用 mock 上游。"""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock-upstream")
