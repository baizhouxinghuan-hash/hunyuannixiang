"""模型列表与映射（任务书 §20）。

模型名称**不从代码里假设**：要么来自 profile（抓包确认），
要么来自 config.yaml 的映射表；两者都为空时返回空列表并标记 UNKNOWN。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.protocol.profile import Profile


@dataclass
class ModelInfo:
    """OpenAI 侧可见模型。"""

    id: str
    upstream: str
    status: str = "unknown"
    owned_by: str = "aistudio"

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "model",
            "created": 0,
            "owned_by": self.owned_by,
            # 以下为本地扩展字段，说明该模型名的证据状态
            "x_upstream_model": self.upstream,
            "x_status": self.status,
        }


def list_models(profile: Profile, *, override: dict[str, str] | None = None) -> list[ModelInfo]:
    """返回可选模型列表。

    * profile.models 为“OpenAI 名称 -> 上游 ID”映射（抓包确认后填写）；
    * override 来自 config.yaml 的 model.mapping（本地自定义别名）；
    两者合并时 override 优先。
    """
    merged: dict[str, str] = dict(profile.models)
    if override:
        merged.update({str(k): str(v) for k, v in override.items()})

    if not merged and profile.default_model:
        return [ModelInfo(id=profile.default_model, upstream=profile.default_model, status="inferred")]

    status = "confirmed" if profile.models else "inferred"
    return [ModelInfo(id=name, upstream=upstream, status=status) for name, upstream in sorted(merged.items())]


def default_model_name(profile: Profile, *, configured: str = "") -> str:
    """本地默认模型名：优先 config.yaml，其次 profile。"""
    if configured:
        return configured
    names = profile.model_ids()
    return names[0] if names else ""
