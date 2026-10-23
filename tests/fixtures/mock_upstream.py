"""【仅测试用】mock 上游：模拟 AI Studio 的行为特征。

⚠️ 这里定义的是**假上游**，不代表 AI Studio 的真实协议。
它的用途是：在没有用户登录会话的情况下，验证
``OpenAI 请求 -> Adapter -> Client -> 流式解析 -> FastAPI -> 错误映射``
这条链路本身是否正确（含 401/429/5xx、断流、多轮上下文）。

行为开关（prompt 关键词触发）：
* 含 “429” -> 返回 429 + Retry-After
* 含 “500” -> 返回 500
* 含 “401” -> 返回 200 + text/html（复刻证据 E9 的未登录表现）
* 含 “断流” -> 流式返回一半后中断
* 含 “空流” -> 直接结束且不发送任何事件
* 含 “SLEEP:<秒>” -> 先睡指定秒数（验证非流式超时 / 排队超时）
* 含 “只思考” -> 只发思考不发正文（验证 fallback_text 兜底）
* 含 “TOOLJSON” -> 回复一个工具调用 JSON（验证本地工具桥接）
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

SPA_HTML = "<!doctype html><html lang=\"zh-CN\"><head><title>Hy AI Studio</title></head><body><div id=\"app\"></div></body></html>"


def create_mock_app(*, require_cookie: str | None = None, rotate_cookie: bool = False) -> FastAPI:
    app = FastAPI(title="mock aistudio upstream")
    app.state.conversations = {}
    app.state.calls = []

    def _check_cookie(request: Request) -> JSONResponse | HTMLResponse | None:
        if require_cookie and require_cookie not in (request.headers.get("cookie") or ""):
            # 复刻历史证据 E9 的行为（真实接口在未登录时返回 401 JSON，这里只测防御性判定）
            return HTMLResponse(SPA_HTML)
        return None

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse(SPA_HTML)

    @app.get("/api/models")
    async def models(request: Request) -> Any:
        if (failure := _check_cookie(request)) is not None:
            return failure
        return {"code": 0, "data": {"models": [{"id": "mock-chat-v1", "name": "Mock Chat"}]}}

    @app.get("/api/new-portal/getuserinfo")
    async def user_info(request: Request) -> Any:
        if (failure := _check_cookie(request)) is not None:
            return failure
        return {"code": 0, "data": {"uin": "10000", "nickname": "tester"}}

    @app.post("/api/new-portal/stop/chat")
    async def stop_chat(request: Request) -> Any:
        payload = await request.json()
        return {"code": 0, "data": {"stopped": True, "conversation_id": payload.get("conversation_id")}}

    @app.post("/api/new-portal/chat")
    async def chat(request: Request) -> Any:
        return await _handle_chat(request)

    @app.post("/api/new-portal/chat/{conversation_id}")
    async def chat_with_id(request: Request, conversation_id: str) -> Any:
        """真实协议形状：发送消息的 path 带 {conversation_id}。"""
        return await _handle_chat(request, conv_from_path=conversation_id)

    @app.post("/api/new-portal/generate/id")
    async def generate_id(request: Request) -> Any:
        """真实协议形状：没有会话时先调这里拿一个新 conversation_id。"""
        payload: dict[str, Any] = await request.json()
        app.state.calls.append(
            {"path": "/api/new-portal/generate/id", "payload": payload, "ts": time.time()}
        )
        if (failure := _check_cookie(request)) is not None:
            return failure
        return {"code": 0, "data": {"id": f"mock-conv-{uuid.uuid4().hex[:8]}", "isNew": True}}

    async def _handle_chat(request: Request, conv_from_path: str | None = None) -> Any:
        payload: dict[str, Any] = await request.json()
        app.state.calls.append(
            {"path": request.url.path, "payload": payload, "ts": time.time()}
        )
        prompt = str(payload.get("prompt") or "")
        conversation_id = (
            conv_from_path
            or payload.get("conversation_id")
            or f"mock-conv-{uuid.uuid4().hex[:8]}"
        )

        if (failure := _check_cookie(request)) is not None:
            return failure

        if "SLEEP:" in prompt:
            # 测试用：模拟上游生成很慢（验证非流式超时 / 排队超时）
            try:
                seconds = float(prompt.split("SLEEP:", 1)[1].strip().split()[0])
            except (IndexError, ValueError):
                seconds = 1.0
            await asyncio.sleep(max(0.0, min(seconds, 60.0)))

        if "401" in prompt:
            return HTMLResponse(SPA_HTML)  # 复刻 E9：未登录返回 SPA
        if "429" in prompt:
            return JSONResponse(
                status_code=429,
                content={"code": 429001, "message": "too many requests"},
                headers={"Retry-After": "3"},
            )
        if "500" in prompt:
            return JSONResponse(status_code=500, content={"code": 500, "message": "boom"})

        history = app.state.conversations.setdefault(conversation_id, [])
        history.append({"role": "user", "content": prompt})
        reply = _generate_reply(history, prompt)
        history.append({"role": "assistant", "content": reply})
        message_id = f"msg-{uuid.uuid4().hex[:8]}"
        headers = {}
        if rotate_cookie:
            headers["Set-Cookie"] = "mock_session=rotated; Path=/"

        if payload.get("stream"):
            return StreamingResponse(
                _stream_body(reply, conversation_id, message_id, prompt),
                media_type="text/event-stream",
                headers=headers,
            )

        return JSONResponse(
            content={
                "code": 0,
                "data": {
                    "content": reply,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "finish_reason": "stop",
                    "usage": {"prompt_tokens": len(prompt), "completion_tokens": len(reply)},
                },
            },
            headers=headers,
        )

    return app


def _extract_name(text: str) -> str | None:
    """从一句话里抽取名字（支持“我叫X”“我的名字叫X”）。"""
    for marker in ("名字叫", "我叫"):
        if marker in text:
            tail = text.split(marker, 1)[1].strip("。.！!？? \n")
            if tail and "什么" not in tail:
                return tail
    return None


def _generate_reply(history: list[dict[str, str]], prompt: str) -> str:
    """极简的“上下文记忆”逻辑，用于验证多轮（任务书 §41 F）。"""
    if "TOOLJSON" in prompt:
        # 测试用：模拟"模型按工具说明输出了 JSON"（供 tool_bridge 解析）
        return '{"tool_calls":[{"name":"get_weather","arguments":{"city":"北京"}}]}'
    stored = None
    for item in history:
        if item["role"] == "user":
            name = _extract_name(item["content"])
            if name:
                stored = name

    if "我叫什么" in prompt:
        return f"你叫{stored}。" if stored else "抱歉，你还没有告诉我你的名字。"
    if _extract_name(prompt):
        return f"你好，{stored or '朋友'}！我记住你的名字了。"
    return f"你说的是：「{prompt}」。这是一条 mock 上游生成的回复。"


async def _stream_body(reply: str, conversation_id: str, message_id: str, prompt: str):
    """SSE 流：message_start -> 若干增量 -> finish -> [DONE]。"""
    yield "event: ping\ndata: {}\n\n"
    yield (
        "event: message_start\n"
        + "data: "
        + json.dumps({"conversation_id": conversation_id, "message_id": message_id}, ensure_ascii=False)
        + "\n\n"
    )
    if "空流" in prompt:
        yield "data: [DONE]\n\n"
        return
    if "只思考" in prompt:
        # 只发思考、不发正文（用于验证 reasoning.mode=fallback_text 的本地兜底）
        yield (
            "data: "
            + json.dumps({"delta": {"reasoning": "（mock 只有思考，没有正文）"}}, ensure_ascii=False)
            + "\n\n"
        )
        yield "data: " + json.dumps({"finish_reason": "stop"}, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"
        return

    chunk_size = 4
    pieces = [reply[i : i + chunk_size] for i in range(0, len(reply), chunk_size)] or [""]
    for index, piece in enumerate(pieces):
        payload = {"delta": {"content": piece}, "index": index}
        if index == 0 and "思考" in prompt:
            payload["delta"]["reasoning"] = "（mock 思考过程）"
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        if "断流" in prompt and index >= 1:
            # 模拟上游中断：直接结束生成器（客户端应看到 stream 结束但无 finish）
            return
    yield f"data: {json.dumps({'finish_reason': 'stop', 'usage': {'prompt_tokens': len(prompt), 'completion_tokens': len(reply)}}, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"
