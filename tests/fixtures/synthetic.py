"""合成抓包数据（用于测试分析工具链，不涉及任何真实会话）。

生成的内容刻意包含：
* Cookie / Authorization / token 等敏感字段（用于验证脱敏）；
* 一次 SSE 流式"发送消息" + 一次非流式"发送消息"（用于差分）；
* 模型列表 / 用户信息 / 上传等噪声请求。
"""

from __future__ import annotations

import json
from typing import Any

from analyzer.network_capture import CaptureRecord, NetworkRecorder

SSE_FRAMES_A = [
    "event: ping\ndata: {}\n\n",
    'data: {"delta": {"content": "你好"}, "index": 0}\n\n',
    'data: {"delta": {"content": "，很高兴"}, "index": 1}\n\n',
    'data: {"delta": {"content": "认识你。"}, "index": 2}\n\n',
    'data: {"finish_reason": "stop"}\n\n',
    "data: [DONE]\n\n",
]

SSE_FRAMES_B = [
    'data: {"delta": {"content": "今天"}, "index": 0}\n\n',
    'data: {"delta": {"content": "天气不错"}, "index": 1}\n\n',
    'data: {"finish_reason": "stop"}\n\n',
    "data: [DONE]\n\n",
]

SENSITIVE_COOKIE = "session_key=SECRET-COOKIE-VALUE-123; uid=10001"
SENSITIVE_TOKEN = "Bearer SECRET-TOKEN-VALUE-abcdef"


def build_synthetic_records() -> list[CaptureRecord]:
    records: list[CaptureRecord] = []

    def add(**kwargs: Any) -> CaptureRecord:
        record = CaptureRecord(seq=len(records) + 1, **kwargs)
        records.append(record)
        return record

    add(
        method="GET",
        url="https://aistudio.tencent.com/api/models",
        request_headers={"accept": "application/json", "cookie": SENSITIVE_COOKIE},
        status=200,
        response_headers={"content-type": "application/json; charset=utf-8"},
        response_body=json.dumps({"code": 0, "data": {"models": [{"id": "hunyuan-x"}]}}),
    )
    add(
        method="GET",
        url="https://aistudio.tencent.com/api/new-portal/getuserinfo",
        request_headers={"accept": "application/json", "authorization": SENSITIVE_TOKEN},
        status=200,
        response_headers={"content-type": "application/json"},
        response_body=json.dumps({"code": 0, "data": {"uin": "10001"}}),
    )
    first = add(
        method="POST",
        url="https://aistudio.tencent.com/api/new-portal/chat",
        request_headers={"content-type": "application/json", "cookie": SENSITIVE_COOKIE},
        request_body=json.dumps(
            {"conversation_id": None, "model": "hunyuan-x", "prompt": "你好", "stream": True, "timestamp": 1758000000000}
        ),
        status=200,
        response_headers={"content-type": "text/event-stream"},
        response_body=None,
    )
    first.stream_frames.extend(SSE_FRAMES_A)

    second = add(
        method="POST",
        url="https://aistudio.tencent.com/api/new-portal/chat",
        request_headers={"content-type": "application/json", "cookie": SENSITIVE_COOKIE},
        request_body=json.dumps(
            {
                "conversation_id": "conv-abc",
                "model": "hunyuan-x",
                "prompt": "今天天气怎么样",
                "stream": True,
                "timestamp": 1758000012345,
            }
        ),
        status=200,
        response_headers={"content-type": "text/event-stream"},
    )
    second.stream_frames.extend(SSE_FRAMES_B)

    add(
        method="POST",
        url="https://aistudio.tencent.com/api/new-portal/stop/chat",
        request_headers={"content-type": "application/json"},
        request_body=json.dumps({"conversation_id": "conv-abc"}),
        status=200,
        response_headers={"content-type": "application/json"},
        response_body=json.dumps({"code": 0}),
    )
    add(
        method="POST",
        url="https://aistudio.tencent.com/api/new-portal/chat/resource/genUploadInfo",
        request_headers={"content-type": "application/json"},
        request_body=json.dumps({"fileName": "a.png", "fileSize": 1024}),
        status=200,
        response_headers={"content-type": "application/json"},
        response_body=json.dumps({"code": 0, "data": {"TmpSecretId": "SECRET-TMP-ID", "TmpSecretKey": "SECRET-TMP-KEY"}}),
    )

    recorder = NetworkRecorder()
    recorder.records = records
    recorder.finalize()
    return recorder.records


def build_synthetic_dicts() -> list[dict[str, Any]]:
    return [record.to_dict(redact=True) for record in build_synthetic_records()]
