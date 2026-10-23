"""Network 捕获（任务书 §3 / §10 / §34）。

**双通道抓包**（这是把“流式机制 UNKNOWN”变成 confirmed 的关键）
--------------------------------------------------------------
通道 1：Chrome DevTools Protocol
    ``Network.requestWillBeSent`` / ``responseReceived`` / ``dataReceived``
    / ``loadingFinished`` / ``eventSourceMessageReceived`` / ``webSocketFrame*``

通道 2：页面内 monkey-patch（``browser.add_init_script``）
    包裹 ``fetch`` / ``XMLHttpRequest`` / ``EventSource`` / ``WebSocket``，
    把请求体与**每一帧原始数据**推到 ``window.__aistudioCapture``。
    对 fetch 响应使用 ``ReadableStream.tee()``，因此即使 CDP 拿不到
    流式 body（SSE 常见），通道 2 也能拿到逐帧内容。

落盘前一律经过 :func:`app.security.redaction.redact_obj`。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from analyzer.fingerprint import (
    content_type_of,
    fingerprint,
    parse_json,
    parse_url,
)
from analyzer.init_scripts import build_init_script
from app.security.redaction import redact_obj


@dataclass
class CaptureRecord:
    """一条被观测到的请求/响应（含流式帧）。"""

    seq: int = 0
    ts: float = field(default_factory=time.time)
    kind: str = "http"  # http | sse | ws
    method: str = "GET"
    url: str = ""
    host: str = ""
    path: str = ""
    query: dict[str, str] = field(default_factory=dict)
    resource_type: str | None = None
    initiator: str | None = None
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: str | None = None
    request_body_json: Any = None
    status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_content_type: str | None = None
    response_body: str | None = None
    response_body_json: Any = None
    timing: dict[str, Any] = field(default_factory=dict)
    stream_frames: list[str] = field(default_factory=list)
    ws_frames: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: str = ""
    fingerprint_struct: dict[str, Any] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    request_id: str | None = None  # CDP requestId，用于内部关联

    # -- 派生 -------------------------------------------------------------
    def finalize(self, *, max_body: int = 200_000) -> "CaptureRecord":
        host, path, query = parse_url(self.url)
        self.host, self.path, self.query = host, path, query
        self.request_body_json = parse_json(self.request_body)
        self.response_body_json = parse_json(self.response_body)
        self.response_content_type = self.response_content_type or content_type_of(self.response_headers)
        if self.response_body and len(self.response_body) > max_body:
            self.notes.append(f"response_body 截断（>{max_body} 字符）")
            self.response_body = self.response_body[:max_body]
        if self.request_body and len(self.request_body) > max_body:
            self.notes.append(f"request_body 截断（>{max_body} 字符）")
            self.request_body = self.request_body[:max_body]
        self.fingerprint = fingerprint(self.__dict__)
        from analyzer.fingerprint import fingerprint_struct

        self.fingerprint_struct = fingerprint_struct(self.__dict__)
        return self

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        data = {
            k: v for k, v in self.__dict__.items() if k not in ("request_body_json", "response_body_json", "fingerprint_struct")
        }
        data["fingerprint_struct"] = self.fingerprint_struct
        data["request_body_json"] = self.request_body_json
        data["response_body_json"] = self.response_body_json
        return redact_obj(data) if redact else data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CaptureRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def is_stream(self) -> bool:
        ct = (self.response_content_type or "").lower()
        return bool(self.stream_frames) or any(token in ct for token in ("event-stream", "ndjson", "stream"))


def summarize(records: Iterable[CaptureRecord]) -> dict[str, Any]:
    """按指纹聚合统计。"""
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record.fingerprint or "unknown"
        bucket = buckets.setdefault(
            key,
            {
                "count": 0,
                "methods": set(),
                "paths": set(),
                "hosts": set(),
                "status": set(),
                "labels": set(),
                "stream": False,
                "sample_url": record.url,
            },
        )
        bucket["count"] += 1
        bucket["methods"].add(record.method)
        bucket["paths"].add(record.path)
        bucket["hosts"].add(record.host)
        bucket["status"].add(record.status)
        if record.classification.get("label"):
            bucket["labels"].add(record.classification["label"])
        bucket["stream"] = bucket["stream"] or record.is_stream

    out: dict[str, Any] = {}
    for key, bucket in buckets.items():
        out[key] = {
            "count": bucket["count"],
            "methods": sorted(bucket["methods"]),
            "paths": sorted(bucket["paths"]),
            "hosts": sorted(bucket["hosts"]),
            "status": sorted(x for x in bucket["status"] if x is not None),
            "labels": sorted(bucket["labels"]),
            "stream": bucket["stream"],
            "sample_url": bucket["sample_url"],
        }
    return dict(sorted(out.items(), key=lambda item: (-item[1]["count"], item[0])))


class NetworkRecorder:
    """双通道抓包器。"""

    def __init__(
        self,
        *,
        url_patterns: list[str] | None = None,
        capture_all: bool = False,
        max_body: int = 200_000,
        logger: Any = None,
    ) -> None:
        self.url_patterns = url_patterns or ["/api/"]
        self.capture_all = capture_all
        self.max_body = max_body
        self.log = logger
        self.records: list[CaptureRecord] = []
        self.errors: list[str] = []
        self._pending: dict[str, CaptureRecord] = {}
        self._ws: dict[str, CaptureRecord] = {}
        self._cdps: list[Any] = []
        self._seq = 0
        self._cursor: dict[int, dict[str, int]] = {}

    # -- 生命周期 ---------------------------------------------------------
    async def attach(self, context: Any, *, script: str | None = None) -> None:
        await context.add_init_script(script=script or build_init_script(self.url_patterns, capture_all=self.capture_all))

    async def attach_page(self, page: Any) -> None:
        try:
            cdp = await page.context.new_cdp_session(page)
            await cdp.send(
                "Network.enable",
                {"maxTotalBufferSize": 200_000_000, "maxResourceBufferSize": 20_000_000},
            )
        except Exception as exc:  # pragma: no cover - 环境相关
            self.errors.append(f"cdp-attach:{type(exc).__name__}:{exc}")
            return

        loop = asyncio.get_running_loop()

        def make_task(coro_factory):
            def handler(params: dict[str, Any]) -> None:
                loop.create_task(coro_factory(params))

            return handler

        cdp.on("Network.requestWillBeSent", make_task(self._on_request))
        cdp.on("Network.responseReceived", make_task(self._on_response))
        cdp.on("Network.eventSourceMessageReceived", make_task(self._on_sse))
        cdp.on("Network.webSocketCreated", make_task(self._on_ws_created))
        cdp.on("Network.webSocketFrameSent", make_task(self._on_ws_frame_sent))
        cdp.on("Network.webSocketFrameReceived", make_task(self._on_ws_frame_received))
        cdp.on("Network.loadingFinished", make_task(self._on_loading_finished))
        self._cdps.append(cdp)
        self._cursor[id(page)] = {"requests": 0, "streams": 0, "ws": 0, "es": 0}

    async def detach(self) -> None:
        for cdp in self._cdps:
            try:
                await cdp.detach()
            except Exception:  # pragma: no cover
                pass
        self._cdps.clear()

    # -- 通道 1 事件处理 --------------------------------------------------
    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def _should_record(self, url: str) -> bool:
        if self.capture_all:
            return True
        return any(pattern in url for pattern in self.url_patterns)

    async def _on_request(self, params: dict[str, Any]) -> None:
        request = params.get("request") or {}
        url = str(request.get("url") or "")
        if not self._should_record(url):
            return
        record = CaptureRecord(
            seq=self._next(),
            ts=time.time(),
            method=str(request.get("method") or "GET").upper(),
            url=url,
            request_headers={str(k): str(v) for k, v in (request.get("headers") or {}).items()},
            request_body=request.get("postData") if isinstance(request.get("postData"), str) else None,
            resource_type=params.get("type"),
            initiator=str((params.get("initiator") or {}).get("type") or "") or None,
            request_id=str(params.get("requestId") or "") or None,
        )
        self._pending[record.request_id or url] = record
        self.records.append(record)

    async def _on_response(self, params: dict[str, Any]) -> None:
        response = params.get("response") or {}
        url = str(response.get("url") or "")
        key = str(params.get("requestId") or url)
        record = self._pending.get(key)
        if record is None:
            if not self._should_record(url):
                return
            record = CaptureRecord(seq=self._next(), ts=time.time(), url=url, request_id=key)
            self._pending[key] = record
            self.records.append(record)
        record.status = response.get("status")
        record.response_headers = {str(k): str(v) for k, v in (response.get("headers") or {}).items()}
        record.response_content_type = str(response.get("mimeType") or "") or content_type_of(record.response_headers)
        timing = response.get("timing") or {}
        record.timing = {
            k: timing.get(k)
            for k in ("requestTime", "sendStart", "sendEnd", "receiveHeadersEnd")
            if timing.get(k) is not None
        }
        if record.is_stream:
            record.kind = "sse"

    async def _on_sse(self, params: dict[str, Any]) -> None:
        key = str(params.get("requestId") or "")
        record = self._pending.get(key)
        if record is None:
            return
        record.kind = "sse"
        data = params.get("data")
        event_name = params.get("eventName")
        line = f"event: {event_name}\n" if event_name else ""
        record.stream_frames.append(f"{line}data: {data}")

    async def _ws_record(self, params: dict[str, Any]) -> CaptureRecord:
        key = str(params.get("requestId") or "")
        record = self._ws.get(key)
        if record is None:
            record = CaptureRecord(
                seq=self._next(),
                ts=time.time(),
                kind="ws",
                method="WS",
                url=str(params.get("url") or ""),
                request_id=key,
            )
            self._ws[key] = record
            self.records.append(record)
        return record

    async def _on_ws_created(self, params: dict[str, Any]) -> None:
        await self._ws_record(params)

    async def _on_ws_frame_sent(self, params: dict[str, Any]) -> None:
        record = await self._ws_record(params)
        record.ws_frames.append({"dir": "c2s", "ts": time.time(), "data": (params.get("response") or {}).get("payloadData")})

    async def _on_ws_frame_received(self, params: dict[str, Any]) -> None:
        record = await self._ws_record(params)
        record.ws_frames.append({"dir": "s2c", "ts": time.time(), "data": (params.get("response") or {}).get("payloadData")})

    async def _on_loading_finished(self, params: dict[str, Any]) -> None:
        key = str(params.get("requestId") or "")
        record = self._pending.get(key)
        if record is None or record.response_body:
            return
        if record.is_stream:
            return  # 流式 body 由通道 2 抓取
        try:
            body = await self._get_body(key)
        except Exception as exc:  # pragma: no cover
            self.errors.append(f"get-body:{type(exc).__name__}")
            return
        if body is not None:
            record.response_body = body

    async def _get_body(self, request_id: str) -> str | None:
        for cdp in self._cdps:
            try:
                result = await cdp.send("Network.getResponseBody", {"requestId": request_id})
            except Exception:
                continue
            body = result.get("body")
            if result.get("base64Encoded"):
                try:
                    import base64

                    return base64.b64decode(body).decode("utf-8", "replace")
                except Exception:
                    return None
            return body
        return None

    # -- 通道 2：从页面取回 ----------------------------------------------
    async def collect_from_page(self, page: Any) -> int:
        """把 ``window.__aistudioCapture`` 里新增的记录并入（增量，避免重复）。"""
        try:
            data = await page.evaluate("() => window.__aistudioCapture || null")
        except Exception as exc:
            self.errors.append(f"page-evaluate:{type(exc).__name__}")
            return 0
        if not data:
            return 0

        cursor = self._cursor.setdefault(id(page), {"requests": 0, "streams": 0, "ws": 0, "es": 0})
        requests = data.get("requests") or []
        for item in requests[cursor["requests"] :]:
            self._merge_page_request(item)
        cursor["requests"] = len(requests)

        streams = data.get("streams") or []
        for item in streams[cursor["streams"] :]:
            self._merge_stream_frame(item)
        cursor["streams"] = len(streams)

        for item in (data.get("es") or [])[cursor["es"] :]:
            self._merge_event_source(item)
        cursor["es"] = len(data.get("es") or [])

        for item in (data.get("ws") or [])[cursor["ws"] :]:
            self._merge_ws(item)
        cursor["ws"] = len(data.get("ws") or [])

        for error in data.get("errors") or []:
            message = str(error)
            if message not in self.errors:
                self.errors.append(message)
        return len(requests)

    def _find_open_record(self, url: str, method: str, ts_sec: float) -> CaptureRecord | None:
        for record in reversed(self.records):
            if getattr(record, "merged_by_page", False) or record.kind != "http":
                continue
            if record.url == url and record.method == method and abs(record.ts - ts_sec) < 60:
                return record
        return None

    def _merge_page_request(self, item: Mapping[str, Any]) -> CaptureRecord:
        url = str(item.get("url") or "")
        method = str(item.get("method") or "GET").upper()
        ts_sec = float(item.get("ts") or 0) / 1000.0 or time.time()
        record = self._find_open_record(url, method, ts_sec)
        if record is None:
            record = CaptureRecord(seq=self._next(), ts=ts_sec, url=url, method=method)
            self.records.append(record)

        record.merged_by_page = True  # type: ignore[attr-defined]
        headers = item.get("headers")
        if isinstance(headers, Mapping) and not record.request_headers:
            record.request_headers = {str(k): str(v) for k, v in headers.items()}
        body = item.get("body")
        if isinstance(body, str) and body and not record.request_body:
            record.request_body = body
        if record.status is None and item.get("status"):
            record.status = item.get("status")
        resp_headers = item.get("responseHeaders")
        if isinstance(resp_headers, Mapping) and not record.response_headers:
            record.response_headers = {str(k): str(v) for k, v in resp_headers.items()}
        if item.get("responseBody") and not record.response_body:
            record.response_body = str(item["responseBody"])
        if item.get("error"):
            record.notes.append(f"页面侧 fetch 异常：{item['error']}")
        return record

    def _record_for_stream(self, url: str) -> CaptureRecord:
        for record in reversed(self.records):
            if record.url == url and record.kind in ("http", "sse"):
                if record.kind == "http":
                    record.kind = "sse"
                return record
        record = CaptureRecord(seq=self._next(), ts=time.time(), url=url, kind="sse", method="POST")
        self.records.append(record)
        return record

    def _merge_stream_frame(self, item: Mapping[str, Any]) -> None:
        url = str(item.get("url") or "")
        if item.get("done") or not url:
            return
        record = self._record_for_stream(url)
        if item.get("chunk"):
            record.stream_frames.append(str(item["chunk"]))
        elif item.get("snapshot"):
            record.stream_frames.append(str(item["snapshot"]))

    def _merge_event_source(self, item: Mapping[str, Any]) -> None:
        url = str(item.get("url") or "")
        if not url:
            return
        record = self._record_for_stream(url)
        frame_type = item.get("type")
        if frame_type == "message":
            record.stream_frames.append(f"data: {item.get('data')}")
        elif frame_type == "server-event":
            record.notes.append(f"页面注册了自定义 SSE 事件监听：{item.get('name')}")
        elif item.get("data") is not None:
            record.stream_frames.append(f"event: {frame_type}\ndata: {item.get('data')}")

    def _merge_ws(self, item: Mapping[str, Any]) -> None:
        url = str(item.get("url") or "")
        if not url:
            return
        record = next((r for r in self.records if r.kind == "ws" and r.url == url), None)
        if record is None:
            record = CaptureRecord(seq=self._next(), ts=time.time(), url=url, kind="ws", method="WS")
            self.records.append(record)
        for frame in item.get("sent") or []:
            record.ws_frames.append({"dir": "c2s", "ts": frame.get("ts"), "data": frame.get("data")})
        for frame in item.get("received") or []:
            record.ws_frames.append({"dir": "s2c", "ts": frame.get("ts"), "data": frame.get("data")})
        for event in item.get("events") or []:
            record.ws_frames.append(
                {"dir": "event", "ts": event.get("ts"), "data": json.dumps(event, ensure_ascii=False)}
            )

    # -- 输出 -------------------------------------------------------------
    def finalize(self, *, classify: bool = True) -> list[CaptureRecord]:
        dicts = [record.finalize(max_body=self.max_body).to_dict(redact=False) for record in self.records]
        if classify:
            from analyzer.request_classifier import classify_records

            for record, enriched in zip(self.records, classify_records(dicts)):
                record.classification = enriched.get("classification", {})
        return self.records

    def to_dicts(self, *, redact: bool = True) -> list[dict[str, Any]]:
        return [record.to_dict(redact=redact) for record in self.records]

    def summary(self) -> dict[str, Any]:
        return summarize(self.records)

    @staticmethod
    def stream_text(record: CaptureRecord) -> str:
        """把流式帧还原成原始文本（用于 SSE / NDJSON 格式分析）。"""
        return "".join(record.stream_frames)



