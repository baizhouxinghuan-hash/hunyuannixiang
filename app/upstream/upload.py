"""文件/图片上传（任务书 §22 / §23）。

当前状态：**未实现可用逻辑**，因为上传链路的请求/响应结构尚未由抓包确认。

已知的**公开证据**（docs/findings.md E7）：
* 端点候选：``POST /api/new-portal/chat/resource/genUploadInfo``
* 之后由打包进前端的 ``cos-sdk`` 用返回的临时密钥直传腾讯 COS
  （``TmpSecretId`` / TmpSecretKey / Token / StartTime / ExpiredTime），
  桶形如 ``hunyuan-*-1258344703.cos.ap-guangzhou.myqcloud.com``

尚需抓包确认（当前 UNKNOWN）：
1. genUploadInfo 的请求体字段（文件名？大小？类型？hash？）；
2. 临时密钥返回结构的确切字段名与层级；
3. 上传完成后的回调/登记接口（把 file_id 与消息关联）；
4. 消息体中引用附件的字段名（``file_id`` / ``attachment_id`` / ``document_id``？）；
5. 支持的文件类型、大小上限、是否有分片。

因此这里不写猜测实现，而是返回说明性的 :class:`ProtocolUnknownError`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.protocol.profile import Profile
from app.upstream.errors import ProtocolUnknownError
from app.upstream.transport import UpstreamTransport

UPLOAD_EVIDENCE_NEEDED = (
    "genUploadInfo 请求体字段",
    "临时密钥返回结构",
    "上传完成后的登记接口",
    "消息体中引用附件的字段名",
)


def upload_status(profile: Profile) -> dict[str, Any]:
    """描述当前上传能力的状态（供 /health、文档与排障使用）。"""
    endpoint = profile.endpoints.get("upload")
    feature = profile.features.get("file_upload")
    return {
        "implemented": False,
        "endpoint": endpoint.path if endpoint else None,
        "endpoint_status": endpoint.status if endpoint else "missing",
        "feature_status": feature.status if feature else "missing",
        "evidence": endpoint.evidence if endpoint else None,
        "evidence_needed": list(UPLOAD_EVIDENCE_NEEDED),
        "reason": "上传链路的请求/响应结构尚未由抓包确认，按项目规则不提供猜测实现",
    }


class UploadClient:
    """占位实现：只提供“明确失败”，不提供猜测行为。"""

    def __init__(self, transport: UpstreamTransport, profile: Profile) -> None:
        self.transport = transport
        self.profile = profile

    async def upload_file(self, path: str | Path, *, purpose: str = "chat") -> dict[str, Any]:
        raise ProtocolUnknownError(
            "upload",
            "文件上传尚未在正常 Web 会话范围内完成验证；"
            f"需要先抓包确认：{'、'.join(UPLOAD_EVIDENCE_NEEDED)}",
        )

    async def upload_bytes(self, data: bytes, *, filename: str, purpose: str = "chat") -> dict[str, Any]:
        raise ProtocolUnknownError(
            "upload",
            "文件上传尚未在正常 Web 会话范围内完成验证（同 upload_file）",
        )
