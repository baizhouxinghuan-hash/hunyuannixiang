"""会话导入与检查（任务书 §31）。

用法::

    python tools/import_session.py                          # 检查 secrets/storage_state.json
    python tools/import_session.py --cookie-header "a=1; b=2" --save
    python tools/import_session.py --probe                  # 顺带探测上游登录态

只会打印**脱敏**后的信息；不修改任何非 secrets/ 目录的文件。
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

from app.config import load_config  # noqa: E402
from app.protocol.profile import load_profile  # noqa: E402
from app.upstream.auth import probe_login_state  # noqa: E402
from app.upstream.errors import AIStudioError  # noqa: E402
from app.upstream.session import SessionState  # noqa: E402
from app.upstream.transport import UpstreamTransport  # noqa: E402


async def _probe(config, session: SessionState) -> dict:
    profile = load_profile(config.profile_path)
    transport = UpstreamTransport(config, profile, session)
    try:
        return await probe_login_state(transport, profile)
    finally:
        await transport.aclose()


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器（供 main 与测试复用）。"""
    parser = argparse.ArgumentParser(description="检查/导入自己的会话")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--storage-state", default=None, help="storage_state 路径（默认取配置）")
    parser.add_argument("--cookie-header", default=None, help="直接给出 Cookie 头字符串")
    parser.add_argument("--save", action="store_true", help="把 cookie 头保存到 secrets/cookie_header.txt")
    parser.add_argument("--probe", action="store_true", help="向上游探测登录状态")
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def main() -> None:
    args = parse_args()

    config = load_config(args.config)
    storage_state = Path(args.storage_state) if args.storage_state else config.storage_state_path

    if args.cookie_header:
        session = SessionState.from_cookie_header(args.cookie_header)
        if args.save:
            target = config.path(config.auth.cookie_header_file)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args.cookie_header, encoding="utf-8")
            print(f"[+] 已写入 {target}（已由 .gitignore 排除）")
    else:
        if not storage_state.exists():
            print(f"[!] 未找到 {storage_state}")
            print("    请先运行：python tools/capture.py --interactive")
            raise SystemExit(2)
        session = SessionState.from_storage_state(storage_state)

    print("[+] 会话摘要（脱敏）：")
    print(json.dumps(session.summary(), ensure_ascii=False, indent=2))

    expired = session.expired()
    if expired:
        print(f"[!] 可能已过期的 cookie：{sorted({c.name for c in expired})}")
        print("    建议重新抓包：python tools/capture.py --interactive")
    else:
        print("[+] 未发现已过期的 cookie（session cookie 无法从存储判断）")

    if args.probe:
        try:
            result = asyncio.run(_probe(config, session))
        except AIStudioError as exc:
            result = {"authenticated": False, "reason": exc.message}
        print("[+] 上游探测：")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
