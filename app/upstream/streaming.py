"""流式解析层（任务书 §9 / §21 / §26）。

设计原则：**解析器与协议事实解耦**
* 传输形态（SSE / NDJSON / JSON lines / WebSocket）由 profile.stream.transport 指定；
* 事件字段映射（哪条路径是增量文本、思考、工具调用）由 profile.stream.event_map 指定；
* 两者都未确认时抛 :class:`ProtocolUnknownError`，绝不猜测 upstream 的帧格式。

统一内部格式（任务书 §21）::

    ChatChunk(text=..., reasoning=..., tool_call=..., finish_reason=...)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable, Mapping, Sequence

from app.protocol.profile import StreamSpec
from app.upstream.errors import AIStudioStreamError, ProtocolUnknownError

_INDEX_RE = re.compile(r"\[(\d+)\]")


@dataclass
class ChatChunk:
    """统一内部输出格式。"""

    text: str | None = None
    reasoning: str | None = None
    tool_call: dict[str, Any] | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    event_name: str | None = None
    raw_event: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            [
                self.text,
                self.reasoning,
                self.tool_call,
                self.finish_reason,
                self.usage,
                self.conversation_id,
                self.message_id,
            ]
        )


@dataclass
class StreamEvent:
    """一个已完整聚合的上游事件。"""

    data: str = ""
    name: str | None = None
    id: str | None = None
    retry: int | None = None
    raw: str = ""


class SSEParser:
    """SSE（text/event-stream）解析器：按空行聚合事件。"""

    name = "sse"

    def __init__(self, done_marker: str | None = None) -> None:
        self.done_marker = done_marker
        self._name: str | None = None
        self._data: list[str] = []
        self._id: str | None = None
        self._retry: int | None = None
        self._raw: list[str] = []

    def feed(self, line: str) -> list[StreamEvent]:
        if line == "":
            return self._flush()
        self._raw.append(line)
        if line.startswith(":"):  # comment / heartbeat
            return []
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            self._name = value
        elif field == "data":
            self._data.append(value)
        elif field == "id":
            self._id = value
        elif field == "retry":
            try:
                self._retry = int(value)
            except ValueError:
                self._retry = None
        return []

    def finish(self) -> list[StreamEvent]:
        return self._flush()

    def _flush(self) -> list[StreamEvent]:
        if not self._data and not self._name:
            self._raw.clear()
            return []
        event = StreamEvent(
            data="\n".join(self._data),
            name=self._name,
            id=self._id,
            retry=self._retry,
            raw="\n".join(self._raw),
        )
        self._name = None
        self._data = []
        self._id = None
        self._retry = None
        self._raw = []
        return [event]


class NDJSONParser:
    """NDJSON（application/x-ndjson）解析器：一行一个 JSON。"""

    name = "ndjson"

    def __init__(self, done_marker: str | None = None) -> None:
        self.done_marker = done_marker

    def feed(self, line: str) -> list[StreamEvent]:
        stripped = line.strip()
        if not stripped:
            return []
        return [StreamEvent(data=stripped, raw=stripped)]

    def finish(self) -> list[StreamEvent]:
        return []


class JSONLinesParser(NDJSONParser):
    name = "json_lines"


_PARSERS: dict[str, type] = {
    "sse": SSEParser,
    "ndjson": NDJSONParser,
    "json_lines": JSONLinesParser,
}


def get_parser(parser_name: str | None) -> type:
    """按名字取解析器；未支持/未确认 -> ProtocolUnknownError。"""
    if not parser_name or parser_name == "unknown":
        raise ProtocolUnknownError("stream.parser", "解析器尚未确认（transport/parser = unknown）")
    parser_cls = _PARSERS.get(parser_name)
    if parser_cls is None:
        raise ProtocolUnknownError(
            "stream.parser",
            f"尚未实现或未确认的解析器 {parser_name!r}；已支持：{sorted(_PARSERS)}",
        )
    return parser_cls


def extract_path(doc: Any, path: str) -> Any:
    """极简 JSON 路径取值：支持 ``$.a.b[0].c``，取不到返回 None。"""
    if not isinstance(path, str) or not path.startswith("$"):
        return None
    node = doc
    for token in re.findall(r"[^.\[\]]+", path[1:]):
        if isinstance(node, Mapping):
            node = node.get(token)
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            try:
                node = node[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if node is None:
            return None
    return node


def first_path(doc: Any, paths: Iterable[str]) -> Any:
    """按顺序取第一个非空值。"""
    for path in paths or []:
        value = extract_path(doc, path)
        if value not in (None, "", [], {}):
            return value
    return None


class EventInterpreter:
    """按 profile.stream 的映射，把上游事件翻译成 :class:`ChatChunk`。"""

    def __init__(self, spec: StreamSpec) -> None:
        self.spec = spec
        self.done_marker = spec.done_marker or "[DONE]"
        self.event_map = {k: list(v or []) for k, v in (spec.event_map or {}).items()}
        self.event_name_map = dict(spec.event_name_map or {})
        self.type_field = getattr(spec, "type_field", None)
        self.type_map = dict(getattr(spec, "type_map", None) or {})

    # -- 判定 -------------------------------------------------------------
    def is_done(self, event: StreamEvent) -> bool:
        data = (event.data or "").strip()
        if data == self.done_marker:
            return True
        if event.name and self.event_name_map.get(event.name) in ("done", "end"):
            return True
        return False

    # -- 翻译 -------------------------------------------------------------
    def chunks(self, event: StreamEvent) -> list[ChatChunk]:
        action = self.event_name_map.get(event.name) if event.name else None
        if action == "ignore":
            return []

        payload: Any = None
        text = (event.data or "").strip()
        if text:
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None

        if payload is None:
            # 非 JSON 且被显式标注为 text 的事件，按纯文本增量处理
            if action == "text" and text:
                return [ChatChunk(text=text, event_name=event.name)]
            return [ChatChunk(raw_event=event.data, event_name=event.name)] if text else []

        def pick(kind: str) -> Any:
            return first_path(payload, self.event_map.get(kind, []))

        # 判别字段（如 $.type = text / thought / meta / traceId）
        kind: str | None = None
        if self.type_field:
            raw_type = _as_text(extract_path(payload, self.type_field))
            if raw_type is not None:
                kind = self.type_map.get(raw_type, raw_type)
        if kind == "ignore":
            return []
        if kind == "meta":
            return [
                ChatChunk(
                    conversation_id=_as_text(pick("conversation_id")),
                    message_id=_as_text(pick("message_id")),
                    event_name=event.name,
                )
            ]
        if kind not in (None, "text", "reasoning", "tool_call"):
            # 未登记的判别值：保留原始事件而不是静默丢弃，便于发现上游变化
            return [ChatChunk(raw_event=event.data, event_name=event.name)]

        # 有判别字段时必须严格分流：
        # type=text 的事件里 msg 是正文；type=thought 的事件里 msg 是思考过程，
        # 两者路径相同（都是 $.msg），因此不能同时取。
        if kind == "reasoning":
            text_value = None
            reasoning_value = _as_text(pick("reasoning"))
        elif kind == "text":
            text_value = _as_text(pick("text"))
            reasoning_value = None
        else:
            # 没有判别字段：沿用按 JSON 路径映射的行为（兼容 mock/其它上游）
            text_value = _as_text(pick("text")) or (_as_text(text) if action == "text" else None)
            reasoning_value = _as_text(pick("reasoning"))

        chunk = ChatChunk(
            text=text_value,
            reasoning=reasoning_value,
            finish_reason=_as_text(pick("finish")),
            usage=_as_dict(pick("usage")),
            conversation_id=_as_text(pick("conversation_id")),
            message_id=_as_text(pick("message_id")),
            tool_call=_as_dict(pick("tool_call")),
            event_name=event.name,
        )
        if chunk.is_empty:
            chunk.raw_event = event.data
        return [chunk]


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _as_dict(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


async def iter_chunks(lines: AsyncIterator[str], spec: StreamSpec) -> AsyncIterator[ChatChunk]:
    """把“上游文本行流”转换为 :class:`ChatChunk` 流。"""
    parser = get_parser(spec.parser or spec.transport)(spec.done_marker)
    interpreter = EventInterpreter(spec)
    seen_any = False

    async for line in lines:
        for event in parser.feed(line):
            if interpreter.is_done(event):
                return
            for chunk in interpreter.chunks(event):
                seen_any = True
                yield chunk

    for event in parser.finish():
        if interpreter.is_done(event):
            return
        for chunk in interpreter.chunks(event):
            seen_any = True
            yield chunk

    if not seen_any:
        raise AIStudioStreamError(
            "上游流式响应结束但未解析出任何事件（可能是 parser 或 event_map 与上游不符）"
        )


def extract_non_stream(payload: Any, response_schema: Mapping[str, Any]) -> ChatChunk:
    """按 response_schema 从非流式 JSON 中提取内容。"""
    def pick(kind: str) -> Any:
        paths = response_schema.get(f"{kind}_paths") or []
        return first_path(payload, list(paths))

    return ChatChunk(
        text=_as_text(pick("content")),
        reasoning=_as_text(pick("reasoning")),
        finish_reason=_as_text(pick("finish")),
        usage=_as_dict(pick("usage")),
        conversation_id=_as_text(pick("conversation_id")),
        message_id=_as_text(pick("message_id")),
        raw_event=json.dumps(payload, ensure_ascii=False)[:2000] if payload is not None else None,
    )

