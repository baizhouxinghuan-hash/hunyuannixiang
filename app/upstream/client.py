"""AIStudioClient —— 任务书 §15 要求的统一客户端。

所有 endpoint 都来自 protocol profile；没有硬编码 URL。
未确认的协议细节一律抛 :class:`ProtocolUnknownError`，不做猜测性请求。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping

from app.config import AppConfig, get_config
from app.logging_setup import get_upstream_logger
from app.protocol.profile import (
    Profile,
    compare_snapshot,
    load_profile,
    write_snapshot,
)
from app.upstream import chat as chat_mod
from app.upstream.auth import build_session, probe_login_state
from app.upstream.conversation import ConversationStore
from app.upstream.errors import ProtocolUnknownError
from app.upstream.models import ModelInfo, default_model_name, list_models
from app.upstream.session import SessionState
from app.upstream.streaming import ChatChunk, extract_non_stream, extract_path, iter_chunks
from app.upstream.transport import UpstreamTransport
from app.upstream.upload import UploadClient, upload_status


class AIStudioClient:
    """AI Studio Web 协议客户端。"""

    def __init__(
        self,
        config: AppConfig,
        profile: Profile,
        *,
        session: SessionState | None = None,
        transport: UpstreamTransport | None = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self.log = get_upstream_logger()
        self.session = session
        self.transport = transport or UpstreamTransport(config, profile, session)
        self.conversations = ConversationStore(
            config.conversation.mode,
            cache_size=config.conversation.cache_size,
            ttl_seconds=config.conversation.ttl_seconds,
        )
        self.uploads = UploadClient(self.transport, profile)

    # -- 构造 -------------------------------------------------------------
    @classmethod
    def from_config(
        cls, config: AppConfig | None = None, *, profile_path: str | None = None, with_session: bool = True
    ) -> "AIStudioClient":
        cfg = config or get_config()
        profile = load_profile(profile_path or cfg.profile_path)
        session: SessionState | None = None
        if with_session and cfg.auth.mode != "none":
            session = build_session(cfg)
        return cls(cfg, profile, session=session)

    async def aclose(self) -> None:
        await self.transport.aclose()

    async def __aenter__(self) -> "AIStudioClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # -- 只读接口 ---------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        return list_models(self.profile, override=self.config.model.mapping)

    def default_model(self) -> str:
        return default_model_name(self.profile, configured=self.config.model.default)

    async def get_user_info(self) -> dict[str, Any]:
        spec = self.profile.require_endpoint("user_info")
        resp = await self.transport.request(spec.method or "GET", spec.path or "")
        body = resp.json()
        return {"status_code": resp.status_code, "raw": body}

    async def get_history(self, conversation_id: str) -> dict[str, Any]:
        """读取历史会话（注意：这不是推理接口，任务书 §8）。"""
        spec = self.profile.require_endpoint("history")
        schema = self.profile.request_schema or {}
        template = schema.get("history_body_template")
        payload = (
            chat_mod.render_template(template, {"conversation_id": conversation_id})
            if isinstance(template, dict) and template
            else {"conversation_id": conversation_id}
        )
        resp = await self.transport.request(spec.method or "POST", spec.path or "", json=payload)
        return {"status_code": resp.status_code, "raw": resp.json()}

    async def probe_login_state(self) -> dict[str, Any]:
        return await probe_login_state(self.transport, self.profile)

    # -- 会话 -------------------------------------------------------------
    async def create_conversation(self, model: str | None = None) -> str:
        """显式创建会话。

        若抓包证明“首条消息会自动建会话”，则本方法应保持不可用（返回明确错误），
        由 :meth:`send_message` 的响应里取 conversation_id。
        """
        spec = self.profile.require_endpoint("create_conversation")
        schema = self.profile.require_schema("request_schema", what="创建会话请求体")
        template = schema.get("create_conversation_body")
        if not isinstance(template, dict) or not template:
            raise ProtocolUnknownError(
                "request_schema.create_conversation_body",
                "创建会话的请求体结构尚未由抓包确认",
            )
        payload = chat_mod.render_template(
            template,
            {
                "agent_id": self.profile.agent_id,
                "model": self.profile.resolve_model(model, override=self.config.model.mapping),
            },
        )
        resp = await self.transport.request(spec.method or "POST", spec.path or "", json=payload)
        body = resp.json()
        for candidate in schema.get("create_conversation_id_paths") or ["$.id", "$.data.id"]:
            value = extract_path(body, candidate)
            if value:
                return str(value)
        raise ProtocolUnknownError(
            "response_schema.conversation_id",
            "创建会话响应里未按配置的路径找到会话 ID",
        )

    async def resolve_conversation(
        self,
        *,
        explicit: str | None = None,
        session_id: str | None = None,
        user: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str | None]:
        """(store_key, 复用的上游 conversation_id 或 None)。"""
        return self.conversations.resolve(
            explicit=explicit, session_id=session_id, user=user, messages=messages
        )

    # -- 请求体构造 -------------------------------------------------------
    def _build_payload(
        self,
        *,
        conversation_id: str | None,
        message: str,
        model: str | None,
        stream: bool,
        messages: list[dict[str, Any]] | None = None,
        extra: Mapping[str, Any] | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        upstream_model = self.profile.resolve_model(model, override=self.config.model.mapping)
        mode = chat_mod.history_mode(self.profile)
        return chat_mod.build_send_payload(
            self.profile,
            conversation_id=conversation_id,
            model=upstream_model,
            prompt=message,
            stream=stream,
            agent_id=self.profile.agent_id,
            messages=messages if mode == "full_history" else None,
            extra=extra,
            effort=effort or self.config.model.effort,
            history_max_chars=self.config.model.history_max_chars,
        )

    def _send_spec(self):
        return self.profile.require_endpoint("send_message")

    async def ensure_conversation(self, conversation_id: str | None, model: str | None = None) -> str:
        """确保有可用的上游会话 ID。

        抓包确认：发送消息的 path 是 /api/new-portal/chat/{conversation_id}，
        因此没有会话时必须先调 /api/new-portal/generate/id 创建。
        """
        spec = self.profile.endpoints.get("send_message")
        path = (spec.path if spec else "") or ""
        if "{conversation_id}" not in path:
            return conversation_id or ""
        if conversation_id:
            return conversation_id
        return await self.create_conversation(model)

    # -- 发送消息（非流式）------------------------------------------------
    async def send_message(
        self,
        conversation_id: str | None,
        message: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatChunk:
        """发送一条消息并返回完整结果。

        抓包确认上游 chat 接口只有 SSE，因此当 response_schema 未确认时，
        这里内部消费流式响应并聚合成完整结果，非流式调用方也能正常使用。

        可用 kwargs：``messages``（full_history 模式）、``extra``（透传参数）、
        ``effort``（上游思考强度，留空取 ``config.model.effort``）。
        """
        spec = self._send_spec()
        # 抓包确认：发送消息的 path 带 {conversation_id}。
        # 直接调用本方法（而非经 API 层）时也要保证有会话，否则会打到 /chat/ 这种残缺路径。
        conversation_id = await self.ensure_conversation(conversation_id, model)
        path = chat_mod.format_path(spec.path or "", conversation_id=conversation_id)
        payload = self._build_payload(
            conversation_id=conversation_id,
            message=message,
            model=model,
            stream=False,
            messages=kwargs.get("messages"),
            extra=kwargs.get("extra"),
            effort=kwargs.get("effort"),
        )
        schema = self.profile.response_schema or {}
        if schema.get("status") == "confirmed":
            resp = await self.transport.request(spec.method or "POST", path, json=payload)
            chunk = extract_non_stream(resp.json(), schema)
            chunk.raw_event = None
            return chunk

        # 抓包确认：上游 chat 接口只有 SSE，没有非流式响应体
        # -> 本地聚合流式结果，避免调用方拿到 501
        self.profile.require_stream()
        merged = ChatChunk()
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        async for part in self.stream_message(conversation_id, message, model=model, **kwargs):
            if part.text:
                text_parts.append(part.text)
            if part.reasoning:
                reasoning_parts.append(part.reasoning)
            if part.tool_call:
                merged.tool_call = part.tool_call
            for attr in ("finish_reason", "usage", "conversation_id", "message_id"):
                value = getattr(part, attr)
                if value:
                    setattr(merged, attr, value)
        merged.text = "".join(text_parts)
        merged.reasoning = "".join(reasoning_parts)
        if not merged.finish_reason:
            merged.finish_reason = "stop"
        return merged

    # -- 发送消息（流式）--------------------------------------------------
    def stream_message(
        self,
        conversation_id: str | None,
        message: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        """返回 :class:`ChatChunk` 异步流。

        注意这是**同步函数**：协议未确认时会在发起 HTTP 之前就抛错，
        便于 FastAPI 在建立 SSE 响应之前返回标准错误 JSON。

        调用方需自行保证 ``conversation_id`` 非空（抓包确认 path 带
        ``{conversation_id}``）；API 层已经先调过 :meth:`ensure_conversation`。
        """
        spec = self._send_spec()
        stream_spec = self.profile.require_stream()
        payload = self._build_payload(
            conversation_id=conversation_id,
            message=message,
            model=model,
            stream=True,
            messages=kwargs.get("messages"),
            extra=kwargs.get("extra"),
            effort=kwargs.get("effort"),
        )
        lines = self.transport.stream_lines(
            spec.method or "POST", chat_mod.format_path(spec.path or "", conversation_id=conversation_id), json=payload
        )
        return iter_chunks(lines, stream_spec)

    # -- 停止生成 ---------------------------------------------------------
    async def stop_generation(self, conversation_id: str | None, *, message_id: str | None = None) -> dict[str, Any]:
        spec = self.profile.require_endpoint("stop_generation")
        payload = chat_mod.build_stop_payload(
            self.profile, conversation_id=conversation_id, message_id=message_id
        )
        resp = await self.transport.request(spec.method or "POST", spec.path or "", json=payload)
        return {"status_code": resp.status_code, "raw": resp.json()}

    # -- 上传（当前不可用，见 upload.py）----------------------------------
    async def upload_file(self, path: str, *, purpose: str = "chat") -> dict[str, Any]:
        return await self.uploads.upload_file(path, purpose=purpose)

    # -- 会话记忆 ---------------------------------------------------------
    def remember_conversation(self, store_key: str, upstream_id: str | None) -> None:
        """缓存上游会话 ID（仅 auto 模式有意义）。

        ``stateless`` 下 ``resolve_conversation`` 永远返回 ``None``，
        缓存写进去也永远不会被读到 —— 直接跳过，避免无意义的内存占用。
        """
        if not upstream_id:
            return
        if self.conversations.mode == "stateless":
            return
        self.conversations.set(store_key, upstream_id)

    # -- 健康 / 协议状态 --------------------------------------------------
    async def protocol_status(self, *, write_snapshot_file: bool = False) -> dict[str, Any]:
        changes = compare_snapshot(self.profile, self.config.snapshot_path)
        summary = self.profile.to_summary()
        summary["upstream_changes"] = changes
        summary["upload"] = upload_status(self.profile)
        if write_snapshot_file:
            write_snapshot(self.profile, self.config.snapshot_path)
            summary["snapshot_written"] = str(self.config.snapshot_path)
        return summary

