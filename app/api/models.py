"""``GET /v1/models`` 与 ``GET /v1/models/{model_id}``（任务书 §16 / §20）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import verify_api_key
from app.upstream.errors import AIStudioModelError
from app.upstream.models import list_models

router = APIRouter(tags=["models"])


@router.get("/v1/models")
async def list_models_endpoint(request: Request) -> dict[str, Any]:
    verify_api_key(request)
    runtime = request.app.state.runtime
    models = list_models(runtime.profile, override=runtime.config.model.mapping)
    payload: dict[str, Any] = {
        "object": "list",
        "data": [m.to_openai() for m in models],
    }
    if not models:
        # 不伪造模型名：明确告知调用方“尚未从抓包确认模型列表”
        payload["x_warning"] = (
            "尚未确认任何上游模型 ID；请先抓包并在 protocol/current/profile.yaml 填写 models"
        )
    return payload


@router.get("/v1/models/{model_id}")
async def retrieve_model(request: Request, model_id: str) -> dict[str, Any]:
    """OpenAI 兼容：部分客户端（含 openai SDK 的 models.retrieve）会调用本接口。"""
    verify_api_key(request)
    runtime = request.app.state.runtime
    models = list_models(runtime.profile, override=runtime.config.model.mapping)
    for model in models:
        if model.id == model_id:
            return model.to_openai()
    if not models:
        raise AIStudioModelError(
            "尚未确认任何上游模型 ID；请先抓包并在 protocol/current/profile.yaml 填写 models"
        )
    raise AIStudioModelError(f"模型 {model_id!r} 不在当前可用列表中：{[m.id for m in models]}")
