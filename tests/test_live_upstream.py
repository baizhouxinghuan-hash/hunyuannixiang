"""真实上游测试（默认跳过）。

启用条件（两者同时满足）：
1. 环境变量 ``AI_STUDIO_LIVE=1``；
2. ``secrets/storage_state.json`` 存在且未过期；
3. ``protocol/current/profile.yaml`` 中相关 endpoint/schema 已由抓包确认为 confirmed。

运行方式::

    set AI_STUDIO_LIVE=1 && python -m pytest tests/test_live_upstream.py -q

**未满足条件时一律 skip**，绝不伪造结果。
"""

from __future__ import annotations

import os

import pytest

from app.config import load_config
from app.protocol.profile import load_profile
from app.upstream.client import AIStudioClient
from tests.conftest import run

pytestmark = pytest.mark.live

LIVE = os.environ.get("AI_STUDIO_LIVE") == "1"


def _live_ready() -> tuple[bool, str]:
    if not LIVE:
        return False, "未设置 AI_STUDIO_LIVE=1"
    config = load_config()
    if not config.storage_state_path.exists():
        return False, f"缺少 {config.storage_state_path}（先跑 tools/capture.py --interactive）"
    profile = load_profile(config.profile_path)
    if not profile.endpoints.get("send_message") or not profile.endpoints["send_message"].known:
        return False, "profile 中 send_message 尚未 confirmed（先回填抓包结果）"
    if not profile.stream.known:
        return False, "profile 中 stream 尚未 confirmed"
    return True, "ok"


def test_live_storage_state_present():
    ready, reason = _live_ready()
    if not ready:
        pytest.skip(reason)


def test_live_login_state():
    ready, reason = _live_ready()
    if not ready:
        pytest.skip(reason)

    async def go():
        config = load_config()
        client = AIStudioClient.from_config(config)
        try:
            return await client.probe_login_state()
        finally:
            await client.aclose()

    result = run(go())
    assert result["authenticated"] is True, result


def test_live_minimal_chat_non_stream():
    ready, reason = _live_ready()
    if not ready:
        pytest.skip(reason)

    async def go():
        config = load_config()
        client = AIStudioClient.from_config(config)
        try:
            return await client.send_message(None, "你好")
        finally:
            await client.aclose()

    chunk = run(go())
    assert chunk.text, "上游返回了空回答"
    print(f"\n[live] 回答：{chunk.text[:200]}\n[live] conversation_id={chunk.conversation_id}")


def test_live_multi_turn_memory():
    ready, reason = _live_ready()
    if not ready:
        pytest.skip(reason)

    async def go():
        config = load_config()
        client = AIStudioClient.from_config(config)
        # 本用例验证的是「上游会话本身能记住上下文」，所以显式切到 auto 模式：
        # 产品默认是 stateless（每次请求新建会话、上一次不再生效）。
        client.conversations.mode = "auto"
        try:
            first = await client.send_message(upstream, "我的名字叫小明。")
            client.remember_conversation(key, first.conversation_id or upstream or "live-test")
            _key, upstream2 = await client.resolve_conversation(explicit="live-test")
            second = await client.send_message(upstream2, "我叫什么？")
            return first, second
        finally:
            await client.aclose()

    first, second = run(go())
    print(f"\n[live] 第一轮：{first.text[:120]}\n[live] 第二轮：{second.text[:120]}")
    assert "小明" in (second.text or ""), "多轮上下文不一致"
