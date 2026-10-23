"""分析工具链测试（任务书 §4 / §6 / §9 / §14 / §35 / §36）。

全部基于**合成抓包数据**（tests/fixtures/synthetic.py），不涉及真实会话。
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from analyzer.diff import DiffEntry, build_report, categorize, diff_json, render_markdown
from analyzer.endpoint_discovery import (
    build_inventory,
    load_records,
    render_inventory_markdown,
    render_summary_markdown,
    write_inventory,
)
from analyzer.fingerprint import body_keys, parse_url
from analyzer.parser import analyze_capture_dir
from analyzer.request_classifier import classify, classify_records
from analyzer.response_parser import analyze_records, candidate_paths, detect, parse_sse_documents
from app.protocol.profile import load_profile
from app.upstream.session import SessionState
from tests.conftest import REAL_PROFILE
from tests.fixtures.synthetic import SSE_FRAMES_A, build_synthetic_dicts, build_synthetic_records


def test_fingerprint_is_stable_and_distinguishes_paths():
    records = build_synthetic_records()
    fps = [record.fingerprint for record in records]
    # 唯一指纹数 = 记录数 - 1（两条“发送消息”请求共享同一指纹）
    assert len(set(fps)) == len(fps) - 1
    chat_fps = [fp for fp in fps if "/api/new-portal/chat " in fp]
    assert len(chat_fps) == 2
    assert chat_fps[0] == chat_fps[1]  # 同类请求 -> 同一指纹
    assert "POST aistudio.tencent.com /api/new-portal/chat application/json" in chat_fps[0]
    assert "text/event-stream" in chat_fps[0]


def test_parse_url_and_body_keys():
    host, path, query = parse_url("https://a.example.com/a/b?x=1&y=2")
    assert (host, path) == ("a.example.com", "/a/b")
    assert query == {"x": "1", "y": "2"}
    assert body_keys({"b": 1, "a": 2}) == ["a", "b"]
    assert body_keys("not-json") == []


def test_classifier_labels_synthetic_records():
    records = build_synthetic_dicts()
    labels = {
        record["path"]: (record.get("classification") or {}).get("label")
        for record in classify_records(records)
    }
    assert labels["/api/new-portal/chat"] == "send_message"
    assert labels["/api/models"] == "model_list"
    assert labels["/api/new-portal/getuserinfo"] == "user_info"
    assert labels["/api/new-portal/stop/chat"] == "stop_generation"
    assert labels["/api/new-portal/chat/resource/genUploadInfo"] == "upload"


def test_classifier_requires_stream_or_chat_fields():
    plain = classify({"method": "POST", "url": "https://a.example.com/api/x", "request_body_json": {"a": 1}})
    assert plain.label == "unknown"
    streamy = classify(
        {
            "method": "POST",
            "url": "https://a.example.com/api/x",
            "request_body_json": {"a": 1},
            "response_content_type": "text/event-stream",
        }
    )
    assert streamy.label == "send_message"
    assert streamy.confidence >= 0.5
    static = classify({"method": "GET", "url": "https://cdn.example.com/a.js", "resource_type": "script"})
    assert static.label == "static_asset"


def test_sse_detection_and_candidate_paths():
    text = "".join(SSE_FRAMES_A)
    docs, sequence, flags = parse_sse_documents(text)
    assert flags["done"] is True
    assert "ping" in sequence
    assert len(docs) == 5  # ping + 3 个增量 + finish

    guess = detect(text)
    assert guess.format == "sse"
    assert guess.confidence > 0.5
    assert guess.has_done_marker
    assert any(item["path"] == "$.delta.content" for item in guess.candidate_paths)
    assert any(item["looks_like_delta"] for item in guess.candidate_paths)


def test_ndjson_and_json_detection():
    assert detect('{"a": 1}').format == "json"
    assert detect('{"a": 1}\n{"a": 2}\n').format == "ndjson"
    assert detect("plain text without structure").format == "unknown"
    assert detect("", kind="http").format == "unknown"
    assert detect("", kind="ws").format == "websocket"


def test_candidate_paths_requires_repetition():
    docs = [{"delta": {"content": "a"}}, {"delta": {"content": "b"}}]
    paths = candidate_paths(docs)
    assert any(item["path"] == "$.delta.content" and item["varying"] for item in paths)
    assert candidate_paths(docs, min_docs=5) == []


def test_diff_report_variable_and_categories():
    records = build_synthetic_dicts()
    report = build_report(records, label="send_message")
    assert report["candidates"] == 2
    assert len(report["pairs"]) == 1
    pair = report["pairs"][0]
    assert "$.prompt" in pair["variable"]
    assert "$.conversation_id" in pair["variable"]
    assert pair["categories"]["消息字段"] == ["$.prompt"]
    assert pair["categories"]["会话字段"] == ["$.conversation_id"]
    assert pair["categories"]["时间字段"] == ["$.timestamp"]

    markdown = render_markdown(report)
    assert "固定字段" in markdown and "变量字段" in markdown


def test_diff_handles_insufficient_candidates():
    report = build_report([], label="send_message")
    assert report["candidates"] == 0
    assert "不足 2 条" in report["note"]


def test_diff_json_nested_and_lists():
    entries = diff_json({"a": {"b": 1}, "list": [1, 2]}, {"a": {"b": 2}, "list": [1]})
    kinds = {entry.path: entry.kind for entry in entries}
    assert kinds["$.a.b"] == "changed"
    assert kinds["$.list.length"] == "changed"


def test_categorize_time_signature_and_random_detection():
    entries = [
        DiffEntry("$.timestamp", "changed", 1758000000000, 1758000012345),
        DiffEntry("$.nonce", "changed", "aaaa", "bbbb"),
        DiffEntry("$.device_hash", "changed", "ab12cd34ef56ab78", "ffffffffffffffff"),
        DiffEntry("$.device_id", "changed", "ab12cd34ef56ab78", "ffffeeeeddddcccc"),
    ]
    categories = categorize(entries)
    assert "$.timestamp" in categories["时间字段"]
    assert "$.nonce" in categories["签名字段"]
    assert "$.device_hash" in categories["签名字段"]  # hash 关键字优先归类为签名
    assert "$.device_id" in categories["随机字段"]


def test_inventory_and_markdown(tmp_path: Path):
    records = build_synthetic_dicts()
    profile = load_profile(REAL_PROFILE)
    inventory = build_inventory(records, profile=profile)
    assert inventory["total_records"] == len(records)
    assert inventory["hosts"] == ["aistudio.tencent.com"]
    assert "send_message" in inventory["by_label"]

    markdown = render_inventory_markdown(inventory, profile=profile)
    assert "| 发送消息 |" in markdown
    assert "| 文件上传 |" in markdown
    assert "candidate(抓包)" in markdown or "inferred" in markdown

    written = write_inventory(inventory, tmp_path / "api_inventory.json", tmp_path / "api_inventory.md")
    assert Path(written["json"]).exists()
    assert Path(written["markdown"]).exists()


def test_summary_markdown_contains_stream_table():
    records = build_synthetic_dicts()
    inventory = build_inventory(records)
    analysis = {"streams": analyze_records(records), "diff": build_report(records)}
    markdown = render_summary_markdown(inventory, analysis, label="unit", errors=["示例错误"])
    assert "unit" in markdown
    assert "抓取到的流式响应" in markdown
    assert "示例错误" in markdown


def test_load_records_and_analyze_capture_dir(tmp_path: Path):
    records = build_synthetic_dicts()
    target = tmp_path / "requests.json"
    target.write_text(
        json.dumps({"meta": {"label": "unit"}, "records": records}, ensure_ascii=False), encoding="utf-8"
    )

    assert len(load_records(target)) == len(records)

    analysis = analyze_capture_dir(tmp_path, profile=load_profile(REAL_PROFILE))
    assert analysis["inventory"]["total_records"] == len(records)
    assert analysis["streams"]["streams"]
    assert analysis["diff"]["candidates"] == 2


def test_replay_selection_and_header_building():
    from tools.replay import build_headers, select_records

    records = build_synthetic_dicts()
    selected = select_records(records, Namespace(seq=3, fingerprint=None, label=None))
    assert len(selected) == 1

    by_label = select_records(records, Namespace(seq=None, fingerprint=None, label="send_message"))
    assert len(by_label) == 2

    headers = build_headers(by_label[0], SessionState.from_cookie_header("session_key=fresh"))
    assert headers["Cookie"] == "session_key=fresh"
    assert "cookie" not in {key.lower() for key in headers if key != "Cookie"}


def test_capture_record_roundtrip_and_stream_text():
    records = build_synthetic_records()
    first = records[2]
    payload = first.to_dict(redact=False)
    from analyzer.network_capture import CaptureRecord, NetworkRecorder

    restored = CaptureRecord.from_dict(payload)
    assert restored.url == first.url
    assert "".join(restored.stream_frames) == "".join(first.stream_frames)
    assert "[DONE]" in NetworkRecorder.stream_text(first)
