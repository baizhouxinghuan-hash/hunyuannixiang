"""最小真实聊天测试（任务书 阶段 6/7）。

用法::

    python tools/smoke_chat.py --prompt "你好，请简单介绍一下自己。"
    python tools/smoke_chat.py --prompt "你好" --no-stream
    python tools/smoke_chat.py --prompt "我叫小明。" --conversation demo
    python tools/smoke_chat.py --prompt "我叫什么？" --conversation demo

只有 profile 里相关 endpoint/schema 已被抓包确认（status: confirmed）时才会真正发请求；
否则会打印明确的 protocol_unknown 提示，这是**刻意设计**，避免猜测上游协议。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.upstream.client import AIStudioClient  # noqa: E402
from app.upstream.errors import AIStudioError  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = AIStudioClient.from_config(config)
    try:
        print(f"[+] 上游：{client.transport.base_url}")
        print(f"[+] 会话：{client.transport.session.summary()}")

        upstream_conv: str | None = None
        if args.conversation:
            if config.conversation.mode == "stateless":
                # 默认模式就是「每次请求新建会话」，此时 --conversation 无意义。
                print(
                    f"[!] conversation.mode=stateless：忽略 --conversation={args.conversation}，"
                    "本次将新建上游会话（要复用会话请把 config.yaml 改成 auto）"
                )
            else:
                _key, existing = await client.resolve_conversation(explicit=args.conversation)
                upstream_conv = existing
        print(f"[+] conversation_id（本地映射）：{upstream_conv!r}")
        upstream_conv = await client.ensure_conversation(upstream_conv, args.model)
        print(f"[+] 上游会话：{upstream_conv}")

        started = time.perf_counter()
        first_token_ms: float | None = None
        pieces: list[str] = []

        if args.stream:
            stream = client.stream_message(upstream_conv, args.prompt, model=args.model)
            async for chunk in stream:
                if chunk.conversation_id and not upstream_conv:
                    upstream_conv = chunk.conversation_id
                    client.remember_conversation(f"explicit:{args.conversation or 'default'}", upstream_conv)
                    print(f"[+] 上游返回 conversation_id：{upstream_conv}")
                if first_token_ms is None and (chunk.text or chunk.reasoning):
                    first_token_ms = round((time.perf_counter() - started) * 1000, 1)
                    print(f"[+] 首 token 延迟：{first_token_ms} ms")
                if chunk.reasoning:
                    print(f"[思考] {chunk.reasoning}", end="", flush=True)
                if chunk.text:
                    print(chunk.text, end="", flush=True)
                    pieces.append(chunk.text)
                if chunk.finish_reason:
                    print(f"\n[+] finish_reason：{chunk.finish_reason}")
        else:
            chunk = await client.send_message(upstream_conv, args.prompt, model=args.model)
            pieces.append(chunk.text or "")
            print("[+] 回答：")
            print(chunk.text)
            print(f"[+] finish_reason：{chunk.finish_reason}，conversation_id={chunk.conversation_id}")

        print(f"\n[+] 总耗时：{round((time.perf_counter() - started) * 1000, 1)} ms，字符数：{sum(len(p) for p in pieces)}")
        return 0
    except AIStudioError as exc:
        print(f"[!] {type(exc).__name__}: {exc.message}")
        if exc.hint:
            print(f"    hint: {exc.hint}")
        return 2
    finally:
        await client.aclose()


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器（供 main 与测试复用）。"""
    parser = argparse.ArgumentParser(description="最小真实聊天测试")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--conversation", default=None, help="本地会话标识（用于多轮）")
    parser.add_argument("--no-stream", dest="stream", action="store_false")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.set_defaults(stream=True)
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
