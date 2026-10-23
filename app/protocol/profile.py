"""协议档案（protocol profile）——本项目“零硬编码 + 绝不猜测”的核心。

设计要点
--------
1. 所有 endpoint / body 模板 / 响应字段路径 / 流式事件映射，都放在 profile YAML 里；
2. 每个字段都带 ``status``，取值严格区分：
   ``confirmed`` / ``partially_confirmed`` / ``inferred`` / ``unknown``；
3. 访问 ``unknown`` 的字段会抛 :class:`ProtocolUnknownError`，
   而不是返回一个“看起来像”的猜测值；
4. ``fingerprint()`` 用于启动时比对上游协议是否变化（任务书 §37）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from app.upstream.errors import ProtocolUnknownError

STATUS_VALUES = ("confirmed", "partially_confirmed", "inferred", "unknown")


class EvidenceField(BaseModel):
    """带证据来源的单个协议事实。"""

    value: Any = None
    status: str = "unknown"
    evidence: str | None = None
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, EvidenceField):
            return data
        if isinstance(data, dict):
            if "value" in data or "status" in data or "evidence" in data:
                return data
            return {"value": data}
        return {"value": data}

    @property
    def known(self) -> bool:
        return self.status in ("confirmed", "partially_confirmed") and self.value not in (None, "", [], {})


class EndpointSpec(BaseModel):
    """一个上游接口的描述。"""

    method: str | None = None
    path: str | None = None
    status: str = "unknown"
    evidence: str | None = None
    notes: str | None = None

    @property
    def known(self) -> bool:
        return self.status == "confirmed" and bool(self.method) and bool(self.path)


class StreamSpec(BaseModel):
    """流式响应描述。"""

    transport: str = "unknown"  # unknown | sse | ndjson | json_lines | websocket
    parser: str | None = None
    status: str = "unknown"
    evidence: str | None = None
    done_marker: str | None = None
    #: 事件字段映射：ChatChunk 字段 -> 候选 JSON 路径列表（按顺序取第一个非空）
    event_map: dict[str, list[str]] = Field(default_factory=dict)
    #: SSE ``event:`` 名 -> 行为（ignore / text / reasoning / tool_call / finish / error）
    event_name_map: dict[str, str] = Field(default_factory=dict)
    #: 事件判别字段（如腾讯混元的 ``$.type``）：同一个 ``msg`` 字段既可承载正文也可承载思考，
    #: 必须靠判别字段区分（抓包证据见 protocol/current/profile.yaml 的 stream.evidence）
    type_field: str | None = None
    #: 判别值 -> 行为（ignore / text / reasoning / meta / tool_call）
    type_map: dict[str, str] = Field(default_factory=dict)
    notes: str | None = None

    @property
    def known(self) -> bool:
        return self.transport != "unknown" and self.status in ("confirmed", "partially_confirmed")


class AuthSpec(BaseModel):
    """认证描述（任务书 §11）。"""

    type: str = "unknown"  # cookie | header | jwt | mixed | unknown
    status: str = "unknown"
    evidence: str | None = None
    cookie_names: list[str] = Field(default_factory=list)
    required_headers: dict[str, str] = Field(default_factory=dict)
    #: 是否存在签名 / nonce / timestamp 校验（未确认前为 unknown）
    signed: EvidenceField = Field(default_factory=EvidenceField)
    notes: str | None = None


class UpstreamSpec(BaseModel):
    base_url: EvidenceField = Field(default_factory=EvidenceField)
    api_prefix: EvidenceField = Field(default_factory=EvidenceField)


class Profile(BaseModel):
    """完整协议档案。"""

    protocol_version: str = "1"
    status: str = "draft"  # draft | captured | frozen
    generated_at: str | None = None
    upstream: UpstreamSpec = Field(default_factory=UpstreamSpec)
    auth: AuthSpec = Field(default_factory=AuthSpec)
    endpoints: dict[str, EndpointSpec] = Field(default_factory=dict)
    #: POST body 模板，支持 {conversation_id} / {model} / {prompt} / {stream} 占位
    request_schema: dict[str, Any] = Field(default_factory=dict)
    #: 非流式响应字段路径
    response_schema: dict[str, Any] = Field(default_factory=dict)
    stream: StreamSpec = Field(default_factory=StreamSpec)
    #: OpenAI 模型名 -> 上游模型 ID
    models: dict[str, str] = Field(default_factory=dict)
    #: 默认上游模型 ID（抓包确认）
    default_model: str = ""
    #: 默认 agentId（抓包确认：腾讯混元为 HunyuanDefault）
    agent_id: str = ""
    #: 功能可用性：image_input / file_upload / web_search / tool_calls / reasoning / stop_generation
    features: dict[str, EvidenceField] = Field(default_factory=dict)
    notes: str | None = None

    # -- endpoint --------------------------------------------------------
    def endpoint(self, name: str) -> EndpointSpec:
        spec = self.endpoints.get(name)
        if spec is None:
            raise ProtocolUnknownError(
                f"endpoint:{name}",
                "protocol profile 中没有该 endpoint 的记录（可能是抓包尚未覆盖的功能）",
            )
        return spec

    def require_endpoint(self, name: str) -> EndpointSpec:
        spec = self.endpoint(name)
        if not spec.known:
            raise ProtocolUnknownError(
                f"endpoint:{name}",
                f"当前状态={spec.status}，method={spec.method!r}，path={spec.path!r}；需要抓包证据才能启用",
            )
        return spec

    # -- schema ----------------------------------------------------------
    def require_schema(self, key: str, *, what: str) -> dict[str, Any]:
        schema = self.request_schema if key == "request_schema" else self.response_schema
        status = schema.get("status") if isinstance(schema, dict) else "missing"
        if not isinstance(schema, dict) or status != "confirmed":
            raise ProtocolUnknownError(f"{key}", f"{what} 尚未由抓包确认（status={status}）")
        return schema

    # -- stream ----------------------------------------------------------
    def require_stream(self) -> StreamSpec:
        if not self.stream.known:
            raise ProtocolUnknownError(
                "stream",
                f"流式传输方式未确认（transport={self.stream.transport}, status={self.stream.status}）",
            )
        if not self.stream.event_map:
            raise ProtocolUnknownError("stream.event_map", "流式事件字段映射为空，无法解析增量内容")
        return self.stream

    # -- models ----------------------------------------------------------
    def model_ids(self) -> list[str]:
        """返回可对外暴露的模型名（OpenAI 客户端看到的名称）。"""
        if self.models:
            return sorted(self.models.keys())
        if self.default_model:
            return [self.default_model]
        return []

    def resolve_model(self, requested: str | None, *, override: dict[str, str] | None = None) -> str:
        """把 OpenAI 侧模型名解析为上游模型 ID。"""
        requested_clean = (requested or "").strip()
        alias = requested_clean or self.default_model
        if not alias:
            raise ProtocolUnknownError("default_model", "尚未确认任何上游模型 ID")
        mapped = override.get(alias) if override else None
        if mapped is None and self.models:
            mapped = self.models.get(alias)
        if mapped is None:
            if requested_clean and self.models and alias not in self.models:
                raise ProtocolUnknownError(
                    "models", f"模型 {alias!r} 未在已确认的模型列表中：{sorted(self.models)}"
                )
            mapped = alias
        return mapped

    # -- 指纹 / 变化检测（任务书 §37）-------------------------------------
    def fingerprint(self) -> dict[str, Any]:
        body_template = self.request_schema.get("body_template")
        return {
            "protocol_version": self.protocol_version,
            "base_url": self.upstream.base_url.value,
            "api_prefix": self.upstream.api_prefix.value,
            "auth_type": self.auth.type,
            "required_headers": sorted(self.auth.required_headers.keys()),
            "endpoints": {
                name: {"method": spec.method, "path": spec.path}
                for name, spec in sorted(self.endpoints.items())
            },
            "request_schema_keys": sorted(body_template.keys()) if isinstance(body_template, dict) else [],
            "response_schema_paths": {
                k: v for k, v in sorted(self.response_schema.items()) if k != "status"
            },
            "stream": {
                "transport": self.stream.transport,
                "parser": self.stream.parser,
                "event_map_keys": sorted(self.stream.event_map.keys()),
            },
            "models": dict(sorted(self.models.items())),
        }

    def fingerprint_hash(self) -> str:
        import hashlib

        blob = json.dumps(self.fingerprint(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def unknown_features(self) -> list[str]:
        unknown = [f"endpoint:{name}" for name, spec in self.endpoints.items() if not spec.known]
        unknown += [f"feature:{name}" for name, feat in self.features.items() if not feat.known]
        if not self.stream.known:
            unknown.append("stream")
        if not self.auth.signed.known:
            unknown.append("auth.signed")
        return sorted(set(unknown))

    def to_summary(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "status": self.status,
            "fingerprint": self.fingerprint_hash(),
            "base_url": self.upstream.base_url.value,
            "confirmed_endpoints": sorted(n for n, s in self.endpoints.items() if s.known),
            "unknown_features": self.unknown_features(),
            "stream": {"transport": self.stream.transport, "status": self.stream.status},
            "models": self.model_ids(),
        }


def load_profile(path: str | Path) -> Profile:
    """从 YAML 加载协议档案。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"protocol profile 不存在: {p}")
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"protocol profile 顶层必须是 mapping: {p}")
    return Profile.model_validate(data)


def write_snapshot(profile: Profile, path: str | Path) -> Path:
    """写出协议指纹快照（供下次启动比对）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprint_hash": profile.fingerprint_hash(),
        "fingerprint": profile.fingerprint(),
    }
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return p


def compare_snapshot(profile: Profile, path: str | Path) -> list[str]:
    """比对当前指纹与快照，返回变化描述列表（空=无变化）。

    快照不存在时返回 ``["snapshot-missing"]``，由调用方决定是否告警。
    """
    p = Path(path)
    if not p.exists():
        return ["snapshot-missing"]
    try:
        with open(p, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError) as exc:
        return [f"snapshot-unreadable:{exc}"]

    changes: list[str] = []
    old = saved.get("fingerprint") or {}
    new = profile.fingerprint()

    if saved.get("fingerprint_hash") != profile.fingerprint_hash():
        changes.append("fingerprint_hash changed")

    old_ep = old.get("endpoints") or {}
    new_ep = new.get("endpoints") or {}
    for name in sorted(set(old_ep) | set(new_ep)):
        if old_ep.get(name) != new_ep.get(name):
            changes.append(f"endpoint {name}: {old_ep.get(name)} -> {new_ep.get(name)}")
    if old.get("stream") != new.get("stream"):
        changes.append(f"stream changed: {old.get('stream')} -> {new.get('stream')}")
    if old.get("models") != new.get("models"):
        changes.append("models changed")
    if old.get("request_schema_keys") != new.get("request_schema_keys"):
        changes.append("request body fields changed")
    if old.get("response_schema_paths") != new.get("response_schema_paths"):
        changes.append("response schema paths changed")
    return changes


