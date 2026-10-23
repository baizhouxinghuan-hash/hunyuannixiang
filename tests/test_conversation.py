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
    assert store.get("a").upstream_id == "conv-a"

    store.set("c", "conv-c")  # 触发容量淘汰
    assert store.stats()["size"] == 2

    store._entries["a"].last_used_at = time.time() - 10
    assert store.get("a") is None
    assert "a" not in store.stats()["keys"]


def test_drop_and_clear():
    store = ConversationStore("auto")
    store.set("k", "conv")
    store.drop("k")
    assert store.get("k") is None
    store.set("k2", "conv2")
    store.clear()
    assert store.stats()["size"] == 0
