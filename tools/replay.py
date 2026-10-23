"""请求重放（任务书 §36）。

只重放**你自己抓到的**请求；默认 dry-run（只打印将要发送的内容）。

用法::

    python tools/replay.py captures/baseline/requests.json --seq 12            # dry-run
    python tools/replay.py captures/baseline/requests.json --seq 12 --send     # 真的发送
    python tools/replay.py captures/baseline/requests.json --fingerprint new-portal/chat --send

注意：重放会真实调用上游接口，可能产生真实额度消耗；请自行确认。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.security.redaction import redact_headers, redact_obj  # noqa: E402
from app.upstream.session import SessionState  # noqa: E402

#: 由客户端自动管理、不应重放的 header
SKIP_HEADERS = {
    "host",
    "content-length",
    "connection",
    ":authority",
    ":method",
    ":path",
    ":scheme",
    "accept-encoding",
    "cookie",
}


def select_records(records: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    selected = records
    if args.seq is not None:
        selected = [record for record in selected if record.get("seq") == args.seq]
    if args.fingerprint:
        selected = [record for record in selected if args.fingerprint in str(record.get("fingerprint") or "")]
    if args.label:
        selected = [
            record for record in selected if (record.get("classification") or {}).get("label") == args.label
        ]
    return selected


def build_headers(record: Mapping[str, Any], session: SessionState) -> dict[str, str]:
    headers = {
        str(key): str(value)
        for key, value in (record.get("request_headers") or {}).items()
        if str(key).lower() not in SKIP_HEADERS
    }
    cookie = session.cookie_header(host=None)
    if cookie:
        headers["Cookie"] = cookie
    return headers


async def replay(record: Mapping[str, Any], *, session: SessionState, stream_limit: int, timeout: float) -> None:
    url = str(record.get("url") or "")
    method = str(record.get("method") or "POST").upper()
    body = record.get("request_body_json")
    headers = build_headers(record, session)

    print("[+] 请求：")
    print(json.dumps(redact_obj({"method": method, "url": url, "headers": headers, "body": body}), ensure_ascii=False, indent=2)[:4000])

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async with client.stream(
            method, url, headers=headers, json=body if body is not None else None
        ) as response:
            print(f"[+] 状态码：{response.status_code}")
            print("[+] 响应头（脱敏）：")
            print(json.dumps(redact_headers(dict(response.headers)), ensure_ascii=False, indent=2))
            content_type = (response.headers.get("content-type") or "").lower()
            if "event-stream" in content_type or "stream" in content_type or "ndjson" in content_type:
                print("[+] 流式内容（前若干行）：")
                count = 0
                async for line in response.aiter_lines():
                    print(f"    {line}")
                    count += 1
                    if count >= stream_limit:
                        break
            else:
                text = (await response.aread()).decode("utf-8", "replace")
                print("[+] 响应体（脱敏，截断）：")
                print(redact_obj(text[:4000]))


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器（供 main 与测试复用）。"""
    parser = argparse.ArgumentParser(description="重放自己抓到的请求")
    parser.add_argument("path", help="captures/<label>/requests.json")
    parser.add_argument("--seq", type=int, default=None, help="按 seq 选择单条")
    parser.add_argument("--fingerprint", default=None, help="按指纹关键字选择")
    parser.add_argument("--label", default=None, help="按分类标签选择（如 send_message）")
    parser.add_argument("--send", action="store_true", help="真的发送（默认 dry-run）")
    parser.add_argument("--stream-limit", type=int, default=30, help="流式响应打印行数上限")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def main() -> None:
    args = parse_args()

    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    records = data.get("records") if isinstance(data, Mapping) else data
    if not records:
        raise SystemExit("没有记录")

    selected = select_records(list(records), args)
    if not selected:
        raise SystemExit("没有匹配的记录（可用 --fingerprint 缩小范围）")

    record = selected[-1]
    print(f"[+] 选中记录 #{record.get('seq')} {record.get('method')} {record.get('url')}")
    if not args.send:
        print("[i] dry-run 模式：只打印将要发送的内容；加 --send 才会真的发送。")
        print(
            json.dumps(
                redact_obj(
                    {
                        "method": record.get("method"),
                        "url": record.get("url"),
                        "headers": record.get("request_headers"),
                        "body": record.get("request_body_json"),
                    }
                ),
                ensure_ascii=False,
                indent=2,
            )[:4000]
        )
        return

    config = load_config(args.config)
    try:
        session = SessionState.from_storage_state(config.storage_state_path)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"无法加载会话：{exc}") from exc

    asyncio.run(replay(record, session=session, stream_limit=args.stream_limit, timeout=args.timeout))


if __name__ == "__main__":
    main()
