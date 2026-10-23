"""注入页面的抓包脚本（通道 2）。

为什么需要它
------------
CDP 的 ``Network.getResponseBody`` 对流式响应（text/event-stream、chunked）
经常取不到内容，而 AI Studio 的“发送消息”几乎一定是流式的。
因此本脚本在页面里包裹 ``fetch`` / ``XMLHttpRequest`` / ``EventSource`` / ``WebSocket``，
用 ``ReadableStream.tee()`` 复制一份响应流，把**每一帧原始数据**推入
``window.__aistudioCapture``，再由 :class:`analyzer.network_capture.NetworkRecorder`
周期性取回。

只抓取 URL 命中 ``__aistudioCaptureConfig.urlPatterns``（默认 ``['/api/']``）的请求，
避免把静态资源塞满内存。
"""

from __future__ import annotations

INIT_SHARED = r"""
(() => {
  if (window.__aistudioCapture) { return; }
  const cfg = window.__aistudioCaptureConfig || {};
  const patterns = cfg.urlPatterns || ['/api/'];
  const all = !!cfg.all;
  const should = (url) => {
    try {
      if (all) { return true; }
      const u = String(url);
      return patterns.some((p) => u.indexOf(p) >= 0);
    } catch (e) { return false; }
  };
  const store = window.__aistudioCapture = {version: 1, requests: [], streams: [], ws: [], es: [], errors: []};
  const clip = (v, n) => {
    try {
      if (v === undefined || v === null) { return null; }
      const s = (typeof v === 'string') ? v : String(v);
      return s.length > n ? s.slice(0, n) + '...[clipped]' : s;
    } catch (e) { return null; }
  };
  const headersToObj = (h) => {
    const out = {};
    try {
      if (!h) { return out; }
      if (typeof h.forEach === 'function' && !Array.isArray(h)) { h.forEach((v, k) => { out[k] = v; }); return out; }
      if (Array.isArray(h)) { h.forEach((kv) => { out[kv[0]] = kv[1]; }); return out; }
      Object.keys(h).forEach((k) => { out[k] = h[k]; });
    } catch (e) {}
    return out;
  };
  const now = () => Date.now();
  const record = (o) => { try { store.requests.push(o); } catch (e) {} };
  window.__aistudioCapture_should = should;
  window.__aistudioCapture_clip = clip;
  window.__aistudioCapture_headers = headersToObj;
  window.__aistudioCapture_record = record;
  window.__aistudioCapture_now = now;
})();
"""


INIT_FETCH_XHR = r"""
(() => {
  const store = window.__aistudioCapture;
  if (!store) { return; }
  const should = window.__aistudioCapture_should;
  const clip = window.__aistudioCapture_clip;
  const headersToObj = window.__aistudioCapture_headers;
  const record = window.__aistudioCapture_record;
  const now = window.__aistudioCapture_now;

  // ---------------- fetch ----------------
  const origFetch = window.fetch;
  if (origFetch) {
    window.fetch = async function (input, init) {
      let url = '', method = 'GET', headers = {}, body = null;
      try {
        if (typeof input === 'string' || input instanceof URL) { url = String(input); }
        else if (input && input.url) { url = String(input.url); }
        method = (init && init.method) || (input && input.method) || 'GET';
        headers = headersToObj((init && init.headers) || (input && input.headers));
        if (init && init.body !== undefined && init.body !== null) {
          body = (typeof init.body === 'string') ? init.body
            : '[non-string body: ' + ((init.body && init.body.constructor) ? init.body.constructor.name : typeof init.body) + ']';
        }
      } catch (e) { store.errors.push('fetch-record:' + e); }

      const started = now();
      let resp;
      try { resp = await origFetch.apply(this, arguments); }
      catch (e) { record({url, method, headers, body, ts: started, error: String(e)}); throw e; }

      const entry = {url, method, headers, body, ts: started, status: resp.status,
                     responseHeaders: headersToObj(resp.headers), responseBody: null, via: 'fetch'};
      if (should(url)) { record(entry); }

      try {
        const ct = (resp.headers.get('content-type') || '');
        if (resp.body && should(url) &&
            (ct.indexOf('event-stream') >= 0 || ct.indexOf('ndjson') >= 0 || ct.indexOf('stream') >= 0)) {
          const pair = resp.body.tee();
          const side = pair[1];
          (async () => {
            try {
              const reader = side.getReader();
              const dec = new TextDecoder('utf-8');
              for (;;) {
                const r = await reader.read();
                if (r.done) { break; }
                const chunk = dec.decode(r.value, {stream: true});
                if (chunk) { store.streams.push({url, ts: now(), chunk}); }
              }
              store.streams.push({url, ts: now(), done: true});
            } catch (e) { store.errors.push('stream-read:' + e); }
          })();
          return new Response(pair[0], {status: resp.status, statusText: resp.statusText, headers: resp.headers});
        }
      } catch (e) { store.errors.push('fetch-tee:' + e); }

      try {
        if (resp.body && should(url)) {
          const ct2 = (resp.headers.get('content-type') || '');
          if (ct2.indexOf('json') >= 0 || ct2.indexOf('text') >= 0) {
            const clone = resp.clone();
            clone.text().then((t) => { entry.responseBody = clip(t, 200000); })
                 .catch((e) => store.errors.push('fetch-clone:' + e));
          }
        }
      } catch (e) { store.errors.push('fetch-clone2:' + e); }
      return resp;
    };
  }

  // ---------------- XMLHttpRequest ----------------
  const XHR = window.XMLHttpRequest;
  if (XHR && XHR.prototype) {
    const origOpen = XHR.prototype.open;
    const origSend = XHR.prototype.send;
    const origSetHeader = XHR.prototype.setRequestHeader;
    XHR.prototype.open = function (method, url) {
      try { this.__cap = {url: String(url), method: String(method || 'GET'), headers: {}, ts: now(), via: 'xhr'}; } catch (e) {}
      return origOpen.apply(this, arguments);
    };
    XHR.prototype.setRequestHeader = function (k, v) {
      try { if (this.__cap) { this.__cap.headers[k] = v; } } catch (e) {}
      return origSetHeader.apply(this, arguments);
    };
    XHR.prototype.send = function (body) {
      const cap = this.__cap;
      if (cap) {
        try {
          cap.body = (typeof body === 'string') ? body : (body == null ? null : '[non-string body]');
          if (should(cap.url)) { record(cap); }
          this.addEventListener('progress', () => {
            try {
              const rt = this.responseType;
              if (rt === '' || rt === 'text') {
                const text = this.responseText || '';
                if (text) { store.streams.push({url: cap.url, ts: now(), xhr: true, snapshot: clip(text.slice(-8000), 8000)}); }
              }
            } catch (e) {}
          });
          this.addEventListener('loadend', () => {
            try {
              cap.status = this.status;
              const rt = this.responseType;
              if (rt === '' || rt === 'text') { cap.responseBody = clip(this.responseText, 200000); }
              else if (rt === 'json') { cap.responseBody = clip(JSON.stringify(this.response), 200000); }
              cap.responseHeadersRaw = clip(this.getAllResponseHeaders(), 4000);
            } catch (e) {}
          });
        } catch (e) { store.errors.push('xhr-send:' + e); }
      }
      return origSend.apply(this, arguments);
    };
  }
})();
"""


INIT_ES_WS = r"""
(() => {
  const store = window.__aistudioCapture;
  if (!store) { return; }
  const clip = window.__aistudioCapture_clip;
  const now = window.__aistudioCapture_now;

  // ---------------- EventSource ----------------
  const ES = window.EventSource;
  if (ES) {
    const Wrapped = function (url, config) {
      const inner = (config === undefined) ? new ES(url) : new ES(url, config);
      const entry = {url: String(url), method: 'GET(EventSource)', ts: now(), frames: []};
      store.es.push(entry);
      inner.addEventListener('message', (ev) => {
        try { entry.frames.push({ts: now(), type: 'message', data: clip(ev && ev.data, 200000)}); }
        catch (e) { store.errors.push('es-message:' + e); }
      });
      const origAdd = inner.addEventListener.bind(inner);
      inner.addEventListener = function (type, fn, opts) {
        try {
          if (type !== 'message' && type !== 'open' && type !== 'error') {
            entry.frames.push({ts: now(), type: 'server-event', name: String(type)});
            return origAdd(type, (ev) => {
              entry.frames.push({ts: now(), type: String(type), data: clip(ev && ev.data, 200000)});
              return fn(ev);
            }, opts);
          }
        } catch (e) { store.errors.push('es-add:' + e); }
        return origAdd(type, fn, opts);
      };
      return inner;
    };
    Wrapped.prototype = ES.prototype;
    ['CONNECTING', 'OPEN', 'CLOSED'].forEach((k) => {
      try { Object.defineProperty(Wrapped, k, {value: ES[k], enumerable: true}); } catch (e) {}
    });
    window.EventSource = Wrapped;
  }

  // ---------------- WebSocket ----------------
  const WS = window.WebSocket;
  if (WS) {
    const WrappedWS = function (url, protocols) {
      const inner = (protocols === undefined) ? new WS(url) : new WS(url, protocols);
      const entry = {url: String(url), protocols: protocols || null, ts: now(), sent: [], received: [], events: []};
      store.ws.push(entry);
      const origSend = inner.send.bind(inner);
      inner.send = function (data) {
        try { entry.sent.push({ts: now(), data: clip(typeof data === 'string' ? data : '[binary]', 200000)}); }
        catch (e) {}
        return origSend(data);
      };
      inner.addEventListener('message', (ev) => {
        try { entry.received.push({ts: now(), data: clip(typeof ev.data === 'string' ? ev.data : '[binary]', 200000)}); }
        catch (e) {}
      });
      ['open', 'close', 'error'].forEach((type) => {
        try { inner.addEventListener(type, (ev) => entry.events.push({ts: now(), type, code: ev && ev.code})); } catch (e) {}
      });
      return inner;
    };
    WrappedWS.prototype = WS.prototype;
    ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach((k) => {
      try { Object.defineProperty(WrappedWS, k, {value: WS[k], enumerable: true}); } catch (e) {}
    });
    window.WebSocket = WrappedWS;
  }
})();
"""


def build_init_script(url_patterns: list[str] | None = None, *, capture_all: bool = False) -> str:
    """拼出完整注入脚本（配置 + fetch/XHR + EventSource/WebSocket 钩子）。"""
    import json as _json

    config = _json.dumps(
        {"urlPatterns": url_patterns or ["/api/"], "all": bool(capture_all)}, ensure_ascii=False
    )
    return f"window.__aistudioCaptureConfig = {config};\n" + INIT_SHARED + INIT_FETCH_XHR + INIT_ES_WS

