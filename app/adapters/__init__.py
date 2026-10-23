"""OpenAI <-> AI Studio 适配层（任务书 §17/§18/§21/§24/§27）。"""

from app.adapters.openai import (
    ChatResult,
    chunk_to_openai_stream,
    collect_passthrough,
    accumulate,
    result_to_openai_response,
    to_upstream_messages,
)

__all__ = [
    "ChatResult",
    "accumulate",
    "chunk_to_openai_stream",
    "collect_passthrough",
    "result_to_openai_response",
    "to_upstream_messages",
]
