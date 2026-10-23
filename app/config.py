"""配置加载（config.yaml -> pydantic 模型，任务书 §29）。

约定
----
* 运行参数（端口、并发、日志）放 config.yaml；
* 协议事实（endpoint / body 模板 / 流式格式 / 模型 ID）放 protocol profile；
* 支持 ``AI_STUDIO_*`` 环境变量覆盖少量关键项。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 1010
    api_keys: list[str] = Field(default_factory=list)
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class UpstreamConfig(BaseModel):
    base_url: str = "https://aistudio.tencent.com"
    api_prefix: str = "/api"
    transport: Literal["httpx", "curl_cffi"] = "httpx"
    impersonate: str = "chrome"
    timeout: float = 60.0
    stream_idle_timeout: float = 180.0
    #: 非流式请求的等待上限（秒）；超时 -> 504 upstream_timeout
    nonstream_timeout: float = 180.0
    browser_headers: bool = True


class AuthConfig(BaseModel):
    storage_state: str = "./secrets/storage_state.json"
    browser_profile_dir: str = "./secrets/browser_profile"
    mode: Literal["storage_state", "cookie_header", "none"] = "storage_state"
    cookie_header_file: str = "./secrets/cookie_header.txt"


class ModelConfig(BaseModel):
    default: str = ""
    mapping: dict[str, str] = Field(default_factory=dict)
    #: 上游思考强度：no_think（快，默认）｜high（慢而深）
    #: 取值来自抓包 getChatConfig.thinkMode.options；客户端 reasoning_effort 可覆盖
    effort: Literal["no_think", "high"] = "no_think"
    #: 拼进 prompt 的 messages 文本上限（字符）；0 = 不限制。
    #: 上游 maxPromptLength=128000（抓包），默认留出充足余量。
    history_max_chars: int = 12000


class ToolBridgeConfig(BaseModel):
    """本地工具桥接（工具协议未抓包确认时的可用方案）。

    * ``off``    : 丢弃 tools + 告警（最诚实，但客户端拿不到 tool_calls）
    * ``bridge`` : 把 tools 渲染进 prompt，再把模型输出的 JSON 解析成标准 tool_calls
    """

    mode: Literal["off", "bridge"] = "bridge"


class ConversationConfig(BaseModel):
    #: stateless = 每次请求都新建上游会话（本次回答与上一次无关），当前默认行为
    mode: Literal["auto", "explicit", "stateless"] = "stateless"
    #: 仅 auto 模式使用；stateless 下不再读写会话缓存
    cache_size: int = 128
    ttl_seconds: int = 3600


class StreamConfig(BaseModel):
    enabled: bool = True


class RateLimitConfig(BaseModel):
    enabled: bool = True
    max_concurrency: int = 1
    #: 排队等待上限（秒）；超时 -> 429 queue_timeout。0 表示无限等待
    max_wait_seconds: float = 120.0


class ReasoningConfig(BaseModel):
    #: fallback_text = 平常同 passthrough，仅当正文为空时用思考兜底（本地策略）
    mode: Literal["passthrough", "drop", "as_text", "fallback_text"] = "passthrough"


class ProtocolConfig(BaseModel):
    profile: str = "./protocol/current/profile.yaml"
    snapshot: str = "./protocol/current/schema_fingerprint.json"
    warn_on_change: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"
    dir: str = "./logs"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 3


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    upstream: UpstreamConfig = Field(default_factory=UpstreamConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    conversation: ConversationConfig = Field(default_factory=ConversationConfig)
    tools: ToolBridgeConfig = Field(default_factory=ToolBridgeConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    reasoning: ReasoningConfig = Field(default_factory=ReasoningConfig)
    protocol: ProtocolConfig = Field(default_factory=ProtocolConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # -- 路径工具 ---------------------------------------------------------
    def path(self, value: str | os.PathLike[str]) -> Path:
        """把相对路径解析为相对项目根目录的绝对路径。"""
        p = Path(value)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @property
    def storage_state_path(self) -> Path:
        return self.path(self.auth.storage_state)

    @property
    def browser_profile_dir(self) -> Path:
        return self.path(self.auth.browser_profile_dir)

    @property
    def profile_path(self) -> Path:
        return self.path(self.protocol.profile)

    @property
    def snapshot_path(self) -> Path:
        return self.path(self.protocol.snapshot)

    @property
    def log_dir(self) -> Path:
        return self.path(self.logging.dir)


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """支持少量环境变量覆盖，便于容器/CI 使用。"""
    env_map = {
        "AI_STUDIO_HOST": ("server", "host"),
        "AI_STUDIO_PORT": ("server", "port"),
        "AI_STUDIO_BASE_URL": ("upstream", "base_url"),
        "AI_STUDIO_TRANSPORT": ("upstream", "transport"),
        "AI_STUDIO_STORAGE_STATE": ("auth", "storage_state"),
        "AI_STUDIO_DEFAULT_MODEL": ("model", "default"),
        "AI_STUDIO_CONVERSATION_MODE": ("conversation", "mode"),
        "AI_STUDIO_LOG_LEVEL": ("logging", "level"),
        "AI_STUDIO_PROFILE": ("protocol", "profile"),
        "AI_STUDIO_EFFORT": ("model", "effort"),
        "AI_STUDIO_NONSTREAM_TIMEOUT": ("upstream", "nonstream_timeout"),
    }
    for env_name, (section, key) in env_map.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        data.setdefault(section, {})[key] = raw
    return data


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """读取 YAML 配置；文件不存在时使用默认值（并保持可用）。"""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误（顶层必须是 mapping）: {cfg_path}")
    return AppConfig.model_validate(_apply_env_overrides(data))


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """进程级配置单例。"""
    return load_config()


def clear_config_cache() -> None:
    get_config.cache_clear()
