"""多轮会话映射（任务书 §19）：OpenAI 请求 -> 上游 conversation_id。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

#: auto 模式下无法识别会话时的兜底 key（单用户本地使用场景）
DEFAULT_KEY = "default"


@dataclass
class ConversationEntry:
    upstream_id: str
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    turns: int = 0


class ConversationStore:
    """内存态会话映射（幂等、带 TTL 与容量上限）。"""

    def __init__(self, mode: str = "stateless", *, cache_size: int = 128, ttl_seconds: int = 3600) -> None:
        """``mode`` 见 :class:`app.config.ConversationConfig`；默认与产品默认一致（每次新建会话）。"""
        self.mode = mode
        self.cache_size = max(1, int(cache_size))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._entries: dict[str, ConversationEntry] = {}

    # -- key 推导 ---------------------------------------------------------
    @staticmethod
    def derive_key(
        *,
        session_id: str | None = None,
        user: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> str:
        """优先 X-Session-Id，其次 OpenAI 的 user 字段，最后用首条 user 消息指纹。"""
        if session_id:
            return f"session:{session_id}"
        if user:
            return f"user:{user}"
        for message in messages or []:
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, list):  # 多模态：取文本部分
                    content = "".join(
                        part.get("text", "") for part in content if isinstance(part, dict)
                    )
                digest = hashlib.sha256(str(content).encode("utf-8")).hexdigest()[:12]
                return f"msg:{digest}"
        return DEFAULT_KEY

    # -- 读写 -------------------------------------------------------------
    def resolve(
        self,
        *,
        explicit: str | None = None,
        session_id: str | None = None,
        user: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str | None]:
        """返回 ``(store_key, existing_upstream_id_or_None)``。

        mode=stateless 时永远返回 ``(key, None)``，即每次新建上游会话。
        """
        key = self.resolve_key(explicit=explicit, session_id=session_id, user=user, messages=messages)
        if self.mode == "stateless":
            return key, None
        if self.mode == "explicit" and not explicit:
            return key, None
        entry = self.get(key)
        return key, (entry.upstream_id if entry else None)

    def resolve_key(
        self,
        *,
        explicit: str | None = None,
        session_id: str | None = None,
        user: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> str:
        if explicit:
            return f"explicit:{explicit}"
        return self.derive_key(session_id=session_id, user=user, messages=messages)

    def get(self, key: str) -> ConversationEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.time() - entry.last_used_at > self.ttl_seconds:
            self._entries.pop(key, None)
            return None
        entry.last_used_at = time.time()
        return entry

    def set(self, key: str, upstream_id: str) -> ConversationEntry:
        entry = self._entries.get(key)
        if entry is None:
            entry = ConversationEntry(upstream_id=upstream_id)
            self._entries[key] = entry
        else:
            entry.upstream_id = upstream_id
            entry.last_used_at = time.time()
        entry.turns += 1
        self._evict()
        return entry

    def drop(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "size": len(self._entries),
            "keys": sorted(self._entries.keys()),
            "ttl_seconds": self.ttl_seconds,
        }

    def _evict(self) -> None:
        now = time.time()
        for key in [k for k, e in self._entries.items() if now - e.last_used_at > self.ttl_seconds]:
            self._entries.pop(key, None)
        while len(self._entries) > self.cache_size:
            oldest = min(self._entries.items(), key=lambda item: item[1].last_used_at)[0]
            self._entries.pop(oldest, None)
