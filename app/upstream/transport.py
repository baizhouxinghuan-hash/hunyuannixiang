"""HTTP 传输层。

两个通道（config.upstream.transport）：
* ``httpx``     —— 默认通道，已在本机验证可用；
* ``curl_cffi`` —— TLS 指纹备选通道（impersonate=chrome），
  仅在 httpx 被上游风控拦截时才需要，**尚未针对真实上游验证**（标注 UNKNOWN）。

与“协议事实”无关的部分（header 名、body 字段）都不在这里，全部来自 protocol profile。
"""

from __future__ import annotations

import json as _json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urljoin, urlparse

import httpx

from app.config import AppConfig
from app.logging_setup import get_upstream_logger
from app.protocol.profile import Profile
from app.security.redaction import redact_headers, redact_obj
from app.upstream.errors import (
    AIStudioUpstreamError,
    error_from_response,
    is_html_response,
)
from app.upstream.session import SessionState

#: 浏览器化基础头（属**推断**：网页端是浏览器环境；抓包后再收敛为最小集）
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
ACCEPT_DEFAULT = "application/json, text/plain, */*"


@dataclass
class UpstreamResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes = b""
    url: str = ""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    @property
    def content_type(self) -> str:
        return self.header("content-type") or ""

    def header(self, name: str, default: str | None = None) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return default

    def json(self) -> Any:
        try:
            return _json.loads(self.text)
        except ValueError:
            return None


class UpstreamTransport:
    """统一的上游 HTTP 客户端（凭证注入 + 错误映射 + 日志脱敏）。"""

    def __init__(
        self,
        config: AppConfig,
        profile: Profile,
        session: SessionState | None = None,
        *,
        http_transport: Any = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self.session = session or SessionState.empty()
        self.log = get_upstream_logger()
        self._client: httpx.AsyncClient | None = None
        self._curl_session: Any = None
        self._log_bodies = False
        #: 测试注入点：httpx.AsyncBaseTransport（例如 ASGITransport 指向 mock 上游）
        self._http_transport = http_transport

    # -- 基础信息 ---------------------------------------------------------
    @property
    def base_url(self) -> str:
        value = self.profile.upstream.base_url.value or self.config.upstream.base_url
        return str(value).rstrip("/")

    @property
    def api_prefix(self) -> str:
        value = self.profile.upstream.api_prefix.value or self.config.upstream.api_prefix
        return "/" + str(value).strip("/")

    @property
    def host(self) -> str:
        return urlparse(self.base_url).hostname or ""

    def url_for(self, path: str) -> str:
        if str(path).startswith("http://") or str(path).startswith("https://"):
            return str(path)
        return urljoin(self.base_url + "/", str(path).lstrip("/"))

    # -- 请求头 -----------------------------------------------------------
    def build_headers(self, extra: Mapping[str, str] | None = None, *, json_body: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": ACCEPT_DEFAULT}
        if self.config.upstream.browser_headers:
            headers.update(
                {
                    "User-Agent": BROWSER_UA,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Referer": self.base_url + "/",
                    "Origin": self.base_url,
                }
            )
        if json_body:
            headers["Content-Type"] = "application/json"

        required = self.profile.auth.required_headers or {}
        headers.update({str(k): str(v) for k, v in required.items()})

        cookie = self.session.cookie_header(self.host)
        if cookie:
            headers["Cookie"] = cookie

        if extra:
            headers.update({str(k): str(v) for k, v in extra.items()})
        return headers

    def auth_summary(self) -> dict[str, Any]:
        return {
            "transport": self.config.upstream.transport,
            "base_url": self.base_url,
            "host": self.host,
            "session": self.session.summary(),
            "required_headers": sorted((self.profile.auth.required_headers or {}).keys()),
        }

    # -- 客户端生命周期 ---------------------------------------------------
    def _ensure_httpx(self) -> httpx.AsyncClient:
        if self._client is None:
            cfg = self.config.upstream
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(
                    cfg.timeout,
                    connect=min(cfg.timeout, 15.0),
                    read=cfg.stream_idle_timeout,
                ),
                follow_redirects=True,
                http2=False,
                headers={"Accept": ACCEPT_DEFAULT},
                transport=self._http_transport,
            )
        return self._client

    def _ensure_curl(self) -> Any:
        if self._curl_session is None:
            try:
                from curl_cffi.requests import AsyncSession  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确指引
                raise AIStudioUpstreamError(
                    "config.upstream.transport=curl_cffi 但 curl_cffi 未安装",
                    hint="pip install curl_cffi==0.14.0，或把 transport 改回 httpx。",
                ) from exc
            self._curl_session = AsyncSession()
        return self._curl_session

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._curl_session is not None:
            try:
                await self._curl_session.close()
            except Exception:  # pragma: no cover
                pass
            self._curl_session = None

    async def __aenter__(self) -> "UpstreamTransport":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # -- 内部工具 ---------------------------------------------------------
    def _absorb_set_cookie(self, resp_headers: Mapping[str, Any]) -> None:
        """上游轮换 cookie 时同步回会话（避免长时间后失效）。"""
        raw = None
        for key, value in resp_headers.items():
            if str(key).lower() == "set-cookie":
                raw = value
                break
        if not raw:
            return
        parts = raw if isinstance(raw, (list, tuple)) else [raw]
        for item in parts:
            first = str(item).split(";")[0]
            if "=" not in first:
                continue
            name, _, value = first.partition("=")
            name, value = name.strip(), value.strip()
            for cookie in self.session.cookies:
                if cookie.name == name:
                    cookie.value = value
                    break

    def _check(self, resp: UpstreamResponse) -> UpstreamResponse:
        if 200 <= resp.status_code < 300 and not is_html_response(resp.content_type, resp.content):
            return resp
        raise error_from_response(
            status_code=resp.status_code,
            headers=resp.headers,
            body=resp.json(),
            content_type=resp.content_type,
            raw_text=resp.text[:2000],
            url=resp.url,
        )

    def _log_request(self, method: str, url: str, payload: Any, status: int | None, elapsed: float) -> None:
        record: dict[str, Any] = {
            "event": "upstream_request",
            "method": method,
            "url": url,
            "status": status,
            "elapsed_ms": round(elapsed * 1000, 1),
            "request_body_keys": sorted(payload.keys()) if isinstance(payload, Mapping) else None,
        }
        if self._log_bodies:
            record["request_body"] = payload
        self.log.info(_json.dumps(redact_obj(record), ensure_ascii=False, default=str))

    # -- 非流式请求 -------------------------------------------------------
    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> UpstreamResponse:
        url = self.url_for(path)
        req_headers = self.build_headers(headers, json_body=json is not None)
        started = time.perf_counter()

        if self.config.upstream.transport == "curl_cffi":
            session = self._ensure_curl()
            try:
                raw = await session.request(
                    method,
                    url,
                    json=json,
                    params=dict(params) if params else None,
                    headers=req_headers,
                    impersonate=self.config.upstream.impersonate,
                    timeout=timeout or self.config.upstream.timeout,
                )
            except Exception as exc:  # noqa: BLE001 - 统一转成上游异常
                raise AIStudioUpstreamError(f"{method} {url} 失败: {type(exc).__name__}") from exc
            resp = UpstreamResponse(
                status_code=raw.status_code,
                headers=dict(raw.headers),
                content=raw.content,
                url=url,
            )
        else:
            client = self._ensure_httpx()
            try:
                raw = await client.request(
                    method,
                    url,
                    json=json,
                    params=dict(params) if params else None,
                    headers=req_headers,
                    timeout=timeout or self.config.upstream.timeout,
                )
            except httpx.HTTPError as exc:
                raise AIStudioUpstreamError(f"{method} {url} 网络失败: {type(exc).__name__}: {exc}") from exc
            resp = UpstreamResponse(
                status_code=raw.status_code,
                headers=dict(raw.headers),
                content=raw.content,
                url=str(raw.url),
            )

        self._log_request(method, url, json, resp.status_code, time.perf_counter() - started)
        self._absorb_set_cookie(resp.headers)
        return self._check(resp)

    # -- 流式请求 ---------------------------------------------------------
    @staticmethod
    async def _iter_lines_bytes(chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
        """按行切分字节流，保留空行（SSE 依赖空行分隔事件）。"""
        buffer = b""
        async for chunk in chunks:
            buffer += chunk
            while True:
                idx = buffer.find(b"\n")
                if idx < 0:
                    break
                line, buffer = buffer[:idx], buffer[idx + 1 :]
                yield line.rstrip(b"\r").decode("utf-8", "replace")
        if buffer:
            yield buffer.rstrip(b"\r").decode("utf-8", "replace")

    async def _stream_raw(
        self,
        method: str,
        url: str,
        payload: Any,
        req_headers: Mapping[str, str],
        timeout: float | None,
    ) -> AsyncIterator[str]:
        if self.config.upstream.transport == "curl_cffi":
            session = self._ensure_curl()
            ctx = session.stream(
                method,
                url,
                json=payload,
                headers=req_headers,
                impersonate=self.config.upstream.impersonate,
                timeout=timeout or self.config.upstream.stream_idle_timeout,
            )
            async with ctx as raw:
                if raw.status_code >= 400 or is_html_response(raw.headers.get("content-type"), None):
                    body = await raw.acontent()
                    raise error_from_response(
                        status_code=raw.status_code,
                        headers=dict(raw.headers),
                        content_type=raw.headers.get("content-type"),
                        raw_text=body.decode("utf-8", "replace")[:2000],
                        url=url,
                    )
                async for line in self._iter_lines_bytes(raw.aiter_content()):
                    yield line
            return

        client = self._ensure_httpx()
        ctx = client.stream(
            method,
            url,
            json=payload,
            headers=req_headers,
            timeout=httpx.Timeout(
                self.config.upstream.timeout,
                connect=min(self.config.upstream.timeout, 15.0),
                read=self.config.upstream.stream_idle_timeout,
            ),
        )
        async with ctx as raw:
            if raw.status_code >= 400 or is_html_response(raw.headers.get("content-type"), None):
                await raw.aread()
                raise error_from_response(
                    status_code=raw.status_code,
                    headers=dict(raw.headers),
                    content_type=raw.headers.get("content-type"),
                    raw_text=raw.text[:2000],
                    url=str(raw.url),
                )
            self._absorb_set_cookie(raw.headers)
            async for line in self._iter_lines_bytes(raw.aiter_bytes()):
                yield line

    async def stream_lines(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> AsyncIterator[str]:
        """以“文本行”粒度产出上游响应（SSE / NDJSON 皆可在此之上解析）。"""
        url = self.url_for(path)
        req_headers = self.build_headers(headers, json_body=json is not None)
        started = time.perf_counter()
        line_count = 0
        first_data_ms: float | None = None
        inner = self._stream_raw(method, url, json, req_headers, timeout)
        try:
            async for line in inner:
                if first_data_ms is None and line.strip():
                    first_data_ms = round((time.perf_counter() - started) * 1000, 1)
                line_count += 1
                yield line
        except httpx.HTTPError as exc:
            raise AIStudioUpstreamError(f"{method} {url} 流式连接失败: {type(exc).__name__}: {exc}") from exc
        finally:
            # 消费方可能提前结束（收到 [DONE] 就 return）：显式关闭内部生成器，
            # 避免 httpx 报 'aclose(): asynchronous generator is already running'
            try:
                await inner.aclose()
            except Exception:  # noqa: BLE001 - 关闭失败不影响已收到的数据
                pass
            self.log.info(
                _json.dumps(
                    redact_obj(
                        {
                            "event": "upstream_stream",
                            "method": method,
                            "url": url,
                            "lines": line_count,
                            "first_line_ms": first_data_ms,
                            "total_ms": round((time.perf_counter() - started) * 1000, 1),
                        }
                    ),
                    ensure_ascii=False,
                )
            )


