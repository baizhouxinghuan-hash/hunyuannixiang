"""响应格式识别（任务书 §9 / §13）。

职责：**只做“像什么格式”的识别与可视化**，不下语义结论。
输出：传输格式 + 判别依据 + SSE 事件名与顺序 + 候选字段路径（含样例，需人工确认）。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass
class FormatGuess:
    format: str = "unknown"
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    event_names: list[str] = field(default_factory=list)
    event_sequence: list[str] = field(default_factory=list)
    has_done_marker: bool = False
    has_id_field: bool = False
    has_retry_field: bool = False
    json_docs: int = 0
    candidate_paths: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
            "event_names": self.event_names,
            "event_sequence": self.event_sequence,
            "has_done_marker": self.has_done_marker,
            "has_id_field": self.has_id_field,
            "has_retry_field": self.has_retry_field,
            "json_docs": self.json_docs,
            "candidate_paths": self.candidate_paths,
        }


def parse_sse_documents(text: str) -> tuple[list[Any], list[str], dict[str, bool]]:
    """把 SSE 文本拆成 (json docs, 事件名序列, 标记位)。"""
    docs: list[Any] = []
    sequence: list[str] = []
    flags = {"done": False, "id": False, "retry": False}
    data_lines: list[str] = []
    current_name: str | None = None

    def flush() -> None:
        nonlocal data_lines, current_name
        if data_lines:
            payload = "\n".join(data_lines)
            if payload.strip() == "[DONE]":
                flags["done"] = True
            else:
                try:
                    docs.append(json.loads(payload))
                except ValueError:
                    pass
            sequence.append(current_name or "message")
        data_lines = []
        current_name = None

    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line == "":
            flush()
            continue
        if line.startswith(":"):
            continue
        field_name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field_name == "event":
            current_name = value
        elif field_name == "data":
            data_lines.append(value)
        elif field_name == "id":
            flags["id"] = True
        elif field_name == "retry":
            flags["retry"] = True
    flush()
    return docs, sequence, flags


def parse_ndjson_documents(text: str) -> list[Any]:
    docs: list[Any] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            docs.append(json.loads(line))
        except ValueError:
            continue
    return docs


def _walk(value: Any, path: str, out: list[tuple[str, Any]]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _walk(item, f"{path}.{key}" if path else str(key), out)
    elif isinstance(value, list):
        for index, item in enumerate(value[:3]):
            _walk(item, f"{path}[{index}]", out)
    else:
        out.append((path, value))


def candidate_paths(docs: Sequence[Any], *, min_docs: int = 2, max_paths: int = 30) -> list[dict[str, Any]]:
    """列出“可能是增量文本”的字段路径（含样例值，**需人工确认**）。"""
    counts: Counter[str] = Counter()
    samples: dict[str, list[Any]] = {}
    for doc in docs:
        pairs: list[tuple[str, Any]] = []
        _walk(doc, "", pairs)
        seen: set[str] = set()
        for path, value in pairs:
            if not path or path in seen:
                continue
            seen.add(path)
            counts[path] += 1
            samples.setdefault(path, [])
            if len(samples[path]) < 3:
                samples[path].append(value)

    candidates: list[dict[str, Any]] = []
    for path, count in counts.most_common():
        if count < min_docs:
            continue
        values = samples.get(path, [])
        stringy = [v for v in values if isinstance(v, str)]
        varying = len(set(map(str, values))) > 1
        candidates.append(
            {
                "path": "$." + path,
                "count": count,
                "varying": varying,
                "samples": [str(v)[:60] for v in values],
                "looks_like_delta": bool(stringy) and varying,
            }
        )
        if len(candidates) >= max_paths:
            break
    return candidates


def detect(text: str, *, kind: str = "http") -> FormatGuess:
    """识别一段响应文本的格式。"""
    guess = FormatGuess()
    if kind == "ws":
        guess.format = "websocket"
        guess.confidence = 0.99
        guess.evidence.append("记录类型为 WebSocket 帧")
        return guess
    if not text:
        guess.evidence.append("空响应")
        return guess

    sse_docs, sse_sequence, flags = parse_sse_documents(text)
    ndjson_docs = parse_ndjson_documents(text)

    sse_score = 0
    if "data:" in text:
        sse_score += 2
    if flags["done"]:
        sse_score += 1
    if sse_sequence:
        sse_score += 1
    if "\n\n" in text:
        sse_score += 1

    if sse_score >= 3:
        guess.format = "sse"
        guess.confidence = min(0.95, 0.5 + 0.1 * sse_score)
        guess.evidence.append(f"含 data: 行、事件分隔与 done 标记（score={sse_score}）")
        guess.event_sequence = sse_sequence
        guess.event_names = sorted(set(sse_sequence))
        guess.has_done_marker = flags["done"]
        guess.has_id_field = flags["id"]
        guess.has_retry_field = flags["retry"]
        guess.json_docs = len(sse_docs)
        guess.candidate_paths = candidate_paths(sse_docs)
        return guess

    if len(ndjson_docs) >= 2:
        guess.format = "ndjson"
        guess.confidence = 0.8
        guess.evidence.append(f"每行都是合法 JSON（{len(ndjson_docs)} 行）")
        guess.json_docs = len(ndjson_docs)
        guess.candidate_paths = candidate_paths(ndjson_docs)
        return guess

    trimmed = text.strip()
    if trimmed.startswith("{") or trimmed.startswith("["):
        guess.format = "json"
        guess.confidence = 0.9
        guess.evidence.append("单一 JSON 文档（更可能是非流式响应）")
        guess.json_docs = 1
        try:
            guess.candidate_paths = candidate_paths([json.loads(trimmed)])
        except ValueError:
            pass
        return guess

    guess.format = "unknown"
    guess.confidence = 0.1
    guess.evidence.append("既不是 SSE、也不是 NDJSON 或 JSON")
    return guess


def analyze_records(records: Any) -> dict[str, Any]:
    """对一批抓包记录做流式格式分析（输入 CaptureRecord 或 dict 均可）。"""
    out: dict[str, Any] = {"streams": [], "websockets": []}
    for record in records:
        kind = getattr(record, "kind", None) or record.get("kind")
        url = getattr(record, "url", None) or record.get("url")
        frames = list(getattr(record, "stream_frames", None) or record.get("stream_frames") or [])
        ws_frames = list(getattr(record, "ws_frames", None) or record.get("ws_frames") or [])
        if kind == "ws" or ws_frames:
            out["websockets"].append(
                {
                    "url": url,
                    "frames": len(ws_frames),
                    "directions": sorted({str(f.get("dir")) for f in ws_frames if isinstance(f, Mapping)}),
                    "sample_c2s": [f.get("data") for f in ws_frames if f.get("dir") == "c2s"][:3],
                    "sample_s2c": [f.get("data") for f in ws_frames if f.get("dir") == "s2c"][:3],
                }
            )
            continue
        if frames:
            text = "".join(frames)
            guess = detect(text, kind="http")
            out["streams"].append({"url": url, "chars": len(text), **guess.to_dict()})
    return out
