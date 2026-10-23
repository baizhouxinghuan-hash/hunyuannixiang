"""上游客户端包。

说明：任务书 §15 建议的 ``client/`` 目录在本项目映射为 ``app/upstream/``：

    client.AIStudioClient          -> app/upstream/client.py
    client.auth / session          -> app/upstream/auth.py / session.py
    client.models / conversation   -> app/upstream/models.py / conversation.py
    client.chat / streaming        -> app/upstream/chat.py / streaming.py
    client.upload / errors         -> app/upstream/upload.py / errors.py
    client.transport               -> app/upstream/transport.py
"""

from app.upstream.errors import (
    AIStudioAuthError,
    AIStudioError,
    AIStudioModelError,
    AIStudioRateLimitError,
    AIStudioRequestError,
    AIStudioStreamError,
    AIStudioUpstreamError,
    ProtocolChangedError,
    ProtocolUnknownError,
    to_openai_error,
)

__all__ = [
    "AIStudioAuthError",
    "AIStudioError",
    "AIStudioModelError",
    "AIStudioRateLimitError",
    "AIStudioRequestError",
    "AIStudioStreamError",
    "AIStudioUpstreamError",
    "ProtocolChangedError",
    "ProtocolUnknownError",
    "to_openai_error",
]
