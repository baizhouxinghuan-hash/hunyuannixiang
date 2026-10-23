"""协议录制器（任务书 §34）。

用法::

    python tools/capture.py --interactive                 # 推荐：手动登录 + 手动操作
    python tools/capture.py --non-interactive --duration 90 --chat "你好" ...

流程：打开有头 Chromium → 你手动登录并操作 → 自动抓包 → 落盘 captures/ 与 secrets/。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analyzer.browser import DEFAULT_URL  # noqa: E402
from analyzer.capture import CaptureOptions, run_capture  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Studio 协议录制器（双通道抓包）",
        epilog=(
            "示例：\n"
            "  python tools/capture.py --label baseline              # 默认：交互模式（手动登录+手动操作）\n"
            "  python tools/capture.py --interactive --har --label baseline\n"
            "  python tools/capture.py --non-interactive --duration 180 --chat \"你好\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--label", default="baseline", help="本次抓包的标签（目录名）")
    parser.add_argument("--url", default=DEFAULT_URL, help="起始地址")
    parser.add_argument("--out", default=str(ROOT / "captures"), help="抓包输出目录")
    parser.add_argument("--secrets", default=str(ROOT / "secrets"), help="凭证目录")

    # 交互模式：默认就是交互（手动登录 + 手动操作），这里显式提供开关便于脚本使用
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--interactive",
        action="store_true",
        help="手动操作模式（默认；与 --non-interactive 互斥）",
    )
    mode.add_argument(
        "--non-interactive",
        action="store_true",
        help="不等待 Enter，配合 --duration/--chat 使用",
    )

    # HAR：默认写，可用 --no-har 关闭
    har = parser.add_mutually_exclusive_group()
    har.add_argument("--har", action="store_true", help="写 session.har（默认）")
    har.add_argument("--no-har", action="store_true", help="不写 HAR")

    parser.add_argument("--headless", action="store_true", help="无头模式（登录会失败，仅用于调试）")
    parser.add_argument("--duration", type=float, default=None, help="自动结束的秒数")
    parser.add_argument("--chat", action="append", default=[], help="要自动发送的消息（可多次）")
    parser.add_argument("--input-selector", default=None, help="输入框选择器（默认值是猜测）")
    parser.add_argument("--send-selector", default=None, help="发送按钮选择器（默认值是猜测）")
    parser.add_argument("--all", action="store_true", help="抓取所有请求（默认只抓 URL 含 /api/ 的）")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="页面内记录回捞间隔（秒）")
    parser.add_argument("--keep-open", action="store_true", help="结束后不关闭浏览器")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    options = CaptureOptions(
        label=args.label,
        url=args.url,
        out_dir=Path(args.out),
        secrets_dir=Path(args.secrets),
        har=not args.no_har,
        headless=args.headless,
        duration=args.duration,
        interactive=not args.non_interactive,
        prompts=list(args.chat),
        input_selector=args.input_selector,
        send_selector=args.send_selector,
        capture_all=args.all,
        poll_interval=args.poll_interval,
        keep_open=args.keep_open,
    )

    result = asyncio.run(run_capture(options))

    print("\n=================== 抓包完成 ===================")
    for note in result.get("notes", []):
        print(f"  - {note}")
    print(f"  - 结束方式：{result.get('ended_by')}")
    session = result.get("session") or {}
    print(f"  - 会话：{json.dumps(session, ensure_ascii=False)}")
    print("\n产物：")
    for name, path in (result.get("artifacts") or {}).items():
        print(f"  - {name}: {path}")

    fingerprints = result.get("summary") or {}
    if fingerprints:
        print("\n指纹汇总（前 20 条）：")
        for index, (key, info) in enumerate(fingerprints.items()):
            if index >= 20:
                break
            print(f"  - [{info.get('count')}] {key}")
            print(f"      标签={info.get('labels')} 流式={info.get('stream')} 状态={info.get('status')}")

    print(
        "\n下一步：\n"
        "  1) 把 captures/<label>/summary.md 与 requests.json 中的内容人工过一遍；\n"
        "  2) python tools/diff_captures.py captures/<label>/requests.json\n"
        "  3) 按证据回填 protocol/current/profile.yaml（status -> confirmed）\n"
        "注意：secrets/ 里的凭证不要提交、不要外发。"
    )


if __name__ == "__main__":
    main()
