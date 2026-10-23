"""会话映射测试（任务书 §19）。"""

from __future__ import annotations

import time

from app.upstream.conversation import DEFAULT_KEY, ConversationStore


def test_derive_key_priority():
    assert ConversationStore.derive_key(session_id="s1", user="u1") == "session:s1"
    assert ConversationStore.derive_key(user="u1") == "user:u1"
    key = ConversationStore.derive_key(messages=[{"role": "user", "content": "你好"}])
    assert key.startswith("msg:")
    assert ConversationStore.derive_key() == DEFAULT_KEY


def test_derive_key_handles_multimodal_content():
    key = ConversationStore.derive_key(
        messages=[{"role": "user", "content": [{"type": "text", "text": "看图"}]}]
    )
    plain = ConversationStore.derive_key(messages=[{"role": "user", "content": "看图"}])
    assert key == plain


def test_auto_mode_reuses_upstream_conversation():
    store = ConversationStore("auto")
    key, upstream = store.resolve(user="u1", messages=[{"role": "user", "content": "hi"}])
    assert upstream is None
    store.set(key, "conv-1")
    key2, upstream2 = store.resolve(user="u1", messages=[{"role": "user", "content": "hi"}])
    assert key2 == key
    assert upstream2 == "conv-1"
    assert store.get(key).turns == 1


def test_stateless_mode_never_reuses():
    store = ConversationStore("stateless")
    key, upstream = store.resolve(user="u1")
    store.set(key, "conv-1")
    _key2, upstream2 = store.resolve(user="u1")
    assert upstream2 is None


def test_explicit_mode_requires_explicit_id():
    store = ConversationStore("explicit")
    key, upstream = store.resolve(user="u1")
    assert upstream is None
    store.set(key, "conv-a")
    _key2, upstream_auto = store.resolve(user="u1")
    assert upstream_auto is None
    key3, upstream_explicit = store.resolve(explicit="thread-1")
    store.set(key3, "conv-b")
    _key4, upstream_b = store.resolve(explicit="thread-1")
    assert upstream_b == "conv-b"


def test_ttl_expiry_and_cache_eviction():
    store = ConversationStore("auto", cache_size=2, ttl_seconds=1)
    store.set("a", "conv-a")
    store.set("b", "conv-b")
    assert store.get("a").upstream_id == "conv-a"  # a 刚被使用 -> b 才是最久未用

    store.set("c", "conv-c")  # 触发容量淘汰：必须淘汰 LRU 的 b，而不是刚用过 a
    assert store.stats()["size"] == 2
    assert store.stats()["keys"] == ["a", "c"]

    store._entries["a"].last_used_at = time.time() - 10
    assert store.get("a") is None
    assert "a" not in store.stats()["keys"]


class _FrozenClock:
    """模拟 Windows 上 Python < 3.13 的粗粒度时钟（同一 tick 内返回值完全相同）。"""

    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def time(self) -> float:
        return self.now


def test_eviction_follows_lru_even_with_coarse_clock(monkeypatch):
    """回归：LRU 淘汰不能依赖 ``time.time()`` 的精度。

    Windows + Python 3.11/3.12 的 ``time.time()`` 只有 ~15.6ms 精度，同一 tick 内
    写入的条目时间戳一模一样；旧实现按时间戳取 ``min`` 会退化成“按插入顺序”，
    把刚 ``get()`` 过的会话踢掉（真实故障：CI 的 windows-latest / py3.11）。
    """
    from app.upstream import conversation as conversation_module

    monkeypatch.setattr(conversation_module, "time", _FrozenClock())
    store = ConversationStore("auto", cache_size=2, ttl_seconds=600)
    store.set("a", "conv-a")
    store.set("b", "conv-b")
    assert store.get("a") is not None
    store.set("c", "conv-c")
    assert store.stats()["keys"] == ["a", "c"]



def test_drop_and_clear():
    store = ConversationStore("auto")
    store.set("k", "conv")
    store.drop("k")
    assert store.get("k") is None
    store.set("k2", "conv2")
    store.clear()
    assert store.stats()["size"] == 0
