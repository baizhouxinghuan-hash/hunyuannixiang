"""本地自检（阶段验收用）：真实起服务 + 真实 HTTP 请求。

用法::

    python tools/selfcheck.py                 # 使用 config.yaml
    python tools/selfcheck.py --port 8123
    python tools/selfcheck.py --offline       # 不调用上游，只做协议状态检查

检查项：
1. ``GET /health``            —— 服务与协议状态
2. ``GET /v1/models``         —— 模型列表（未确认时为空 + x_warning）
3. ``POST /v1/chat/completions``（非流式）
   —— profile 已确认：必须返回**真实回答**；未确认：必须 501 protocol_unknown
4. ``POST /v1/chat/completions``（stream=true）
   —— 已确认：必须是 ``text/event-stream`` 且以 ``[DONE]`` 收尾；未确认：必须 501
5. ``GET /v1/protocol``       —— 协议摘要

说明：

* ``server.api_keys`` 非空时自动带 ``Authorization: Bearer <key>``，否则 ``/v1/*`` 会 401
  （这是预期行为，不是自检失败）；
* 已确认协议时第 3/4 项会**真实调用上游**（消耗额度），用 ``--offline`` 可跳过。

输出末尾会打印 PASS/FAIL 汇总，便于作为阶段验收证据。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.main import create_app  # noqa: E402


class _Server(uvicorn.Server):
    """在后台线程里跑 uvicorn，便于自检脚本单进程完成验证。"""

    def install_signal_handlers(self) -> None:  # pragma: no cover - 后台线程不能装信号处理
        return


def start_server(app, host: str, port: int) -> tuple[_Server, threading.Thread]:
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = _Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while time.time() < deadline:
        if getattr(server, "started", False):
            return server, thread
        time.sleep(0.2)
    raise RuntimeError("服务启动超时")


def auth_headers(config: Any) -> dict[str, str]:
    """``server.api_keys`` 非空时必须带 Key，否则 ``/v1/*`` 会 401。"""
    keys = list(config.server.api_keys)
    return {"Authorization": f"Bearer {keys[0]}"} if keys else {}


def chat_ready(profile: Any) -> bool:
    """发送消息的 endpoint 与流式解析是否都已由抓包确认。"""
    send = profile.endpoints.get("send_message")
    return bool(send and send.known) and bool(profile.stream and profile.stream.known)


def main() -> int:
    parser = argparse.ArgumentParser(description="本地自检 + 验收证据")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="不调用上游：跳过聊天检查（只验证 /health、/v1/models、/v1/protocol）",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.logging)
    app = create_app(config)
    profile = app.state.runtime.profile
    headers = auth_headers(config)
    confirmed = chat_ready(profile)
    base = f"http://{args.host}:{args.port}"

    print(f"[i] profile：{config.profile_path}")
    print(f"[i] 上游：{config.upstream.base_url}（会话模式：{config.conversation.mode}）")
    if headers:
        print("[i] 已启用本地鉴权（server.api_keys 非空）：/v1/* 请求会带上 Authorization 头")
    if args.offline:
        print("[i] --offline：跳过聊天检查，不会调用上游")
    elif confirmed:
        print("[i] profile 已由抓包确认：聊天检查将**真实调用上游**（消耗额度）")
    else:
        print("[i] profile 尚未确认：聊天检查期望 501 protocol_unknown")

    server, _thread = start_server(app, args.host, args.port)
    results: list[tuple[str, bool, str]] = []
    try:
        with httpx.Client(base_url=base, timeout=60.0) as client:
            # 1) /health
            health = client.get("/health", params={"probe": "false"})
            body = health.json()
            # status 取值：ok / ok_with_unknown_protocol / degraded（未登录会话时）
            ok_status = body.get("status") in ("ok", "ok_with_unknown_protocol", "degraded")
            results.append(
                (
                    "GET /health",
                    health.status_code == 200 and ok_status,
                    json.dumps(
                        {
                            "status": body.get("status"),
                            "authenticated": body.get("authenticated"),
                            "auth_error": body.get("auth_error"),
                            "protocol": body.get("protocol"),
                            "unknown_features_count": len(body.get("unknown_features") or []),
                            "upload_implemented": (body.get("upload") or {}).get("implemented"),
                        },
                        ensure_ascii=False,
                    )[:700],
                )
            )

            # 2) /v1/models
            models = client.get("/v1/models", headers=headers)
            model_body = models.json()
            results.append(
                (
                    "GET /v1/models",
                    models.status_code == 200 and "data" in model_body,
                    json.dumps(model_body, ensure_ascii=False)[:300],
                )
            )
            model_ids = [
                str((item or {}).get("id") or "")
                for item in (model_body.get("data") or [])
                if isinstance(item, dict)
            ]
            model_id = next((item for item in model_ids if item), "") or str(config.model.default or "")

            if args.offline:
                print("[i] 已跳过第 3/4 项聊天检查（--offline）")
            elif not confirmed:
                # 3) 非流式聊天：未确认协议时必须 501 且不给假回答
                chat = client.post(
                    "/v1/chat/completions",
                    json={"model": "hunyuan", "messages": [{"role": "user", "content": "你好"}]},
                    headers=headers,
                )
                chat_body = chat.json()
                results.append(
                    (
                        "POST /v1/chat/completions (未确认协议 -> 501)",
                        chat.status_code == 501
                        and chat_body.get("error", {}).get("code") == "protocol_unknown"
                        and "choices" not in chat_body,
                        json.dumps(chat_body, ensure_ascii=False)[:400],
                    )
                )

                # 4) 流式聊天：同样必须 501，而不是先 200 再断流
                stream = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "hunyuan",
                        "stream": True,
                        "messages": [{"role": "user", "content": "你好"}],
                    },
                    headers=headers,
                )
                results.append(
                    (
                        "POST /v1/chat/completions?stream=true (未确认协议 -> 501)",
                        stream.status_code == 501
                        and "text/event-stream" not in stream.headers.get("content-type", ""),
                        f"status={stream.status_code} content-type={stream.headers.get('content-type')}",
                    )
                )
            elif not model_id:
                results.append(
                    (
                        "POST /v1/chat/completions (已确认 -> 真实回答)",
                        False,
                        "无法确定模型名：/v1/models 为空且 config.model.default 未设置",
                    )
                )
            else:
                # 3) 非流式：协议已确认，必须返回真实回答（本地聚合上游 SSE）
                started = time.perf_counter()
                chat = client.post(
                    "/v1/chat/completions",
                    json={"model": model_id, "messages": [{"role": "user", "content": "你好"}]},
                    headers=headers,
                )
                chat_body = chat.json()
                try:
                    content = str(chat_body["choices"][0]["message"]["content"] or "")
                except (KeyError, IndexError, TypeError):
                    content = ""
                results.append(
                    (
                        "POST /v1/chat/completions (已确认 -> 真实回答)",
                        chat.status_code == 200 and bool(content.strip()),
                        json.dumps(
                            {
                                "status": chat.status_code,
                                "model": model_id,
                                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                                "conversation_id": chat_body.get("conversation_id"),
                                "content": content[:120],
                            },
                            ensure_ascii=False,
                        )[:400],
                    )
                )

                # 4) 流式：必须是 SSE，且以 [DONE] 收尾
                started = time.perf_counter()
                with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json={
                        "model": model_id,
                        "stream": True,
                        "messages": [{"role": "user", "content": "你好"}],
                    },
                    headers=headers,
                ) as stream:
                    stream_type = stream.headers.get("content-type", "")
                    stream_body = "".join(stream.iter_text())
                results.append(
                    (
                        "POST /v1/chat/completions?stream=true (已确认 -> SSE)",
                        "text/event-stream" in stream_type
                        and "chat.completion.chunk" in stream_body
                        and "[DONE]" in stream_body,
                        json.dumps(
                            {
                                "content_type": stream_type,
                                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                                "bytes": len(stream_body),
                                "has_done": "[DONE]" in stream_body,
                            },
                            ensure_ascii=False,
                        )[:400],
                    )
                )

            # 5) 协议摘要
            protocol = client.get("/v1/protocol")
            proto_body = protocol.json()
            results.append(
                (
                    "GET /v1/protocol",
                    protocol.status_code == 200 and "confirmed_endpoints" in proto_body,
                    json.dumps(proto_body, ensure_ascii=False)[:400],
                )
            )
    finally:
        server.should_exit = True

    print("\n==================== 自检结果 ====================")
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        print(f"       {detail}")
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n合计：{len(results) - len(failed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "，".join(failed))
        print("提示：401 -> 请求未带 Key（config.yaml: server.api_keys，本脚本会自动带上）；")
        print("      501 -> profile 尚未由抓包确认（见 docs/protocol_captured.md）；")
        print("      200 但无内容 -> 检查登录会话是否过期（python tools/healthcheck.py）。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
