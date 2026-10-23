"""健康检查（任务书 §32 / §37）。

用法::

    python tools/healthcheck.py                 # 检查本地服务 /health
    python tools/healthcheck.py --direct        # 直连上游：可达性 + 登录态 + 协议指纹
    python tools/healthcheck.py --direct --write-snapshot
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.protocol.profile import compare_snapshot, load_profile, write_snapshot  # noqa: E402
from app.upstream.auth import build_session  # noqa: E402
from app.upstream.errors import AIStudioError  # noqa: E402
from app.upstream.upload import upload_status  # noqa: E402


async def direct_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    profile = load_profile(config.profile_path)
    print("[+] 协议档案：")
    print(json.dumps(profile.to_summary(), ensure_ascii=False, indent=2)[:3000])

    changes = compare_snapshot(profile, config.snapshot_path)
    if changes == ["snapshot-missing"]:
        print("[i] 尚无协议指纹快照，本次将创建")
        write_snapshot(profile, config.snapshot_path)
    elif changes:
        print("WARNING: upstream protocol changed -> " + "; ".join(changes))
    else:
        print("[+] 协议指纹与快照一致")

    if args.write_snapshot:
        write_snapshot(profile, config.snapshot_path)
        print(f"[+] 已更新快照：{config.snapshot_path}")

    from app.upstream.transport import UpstreamTransport  # 延迟导入，避免无关依赖

    session = None
    try:
        session = build_session(config) if config.auth.mode != "none" else None
    except AIStudioError as exc:
        print(f"[!] 会话加载失败：{exc.message}")

    transport = UpstreamTransport(config, profile, session)
    try:
        # 基础可达性：直接 GET 首页（HTML 回退属于正常现象，不算失败）
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
                response = await client.get(transport.base_url + "/")
            print(f"[+] 上游可达：HTTP {response.status_code}，content-type={response.headers.get('content-type')}")
        except httpx.HTTPError as exc:
            print(f"[!] 上游不可达/被拦截：{type(exc).__name__}: {exc}")

        from app.upstream.auth import probe_login_state

        result = await probe_login_state(transport, profile)
        print("[+] 登录态探测：")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await transport.aclose()

    print("[+] 上传能力：")
    print(json.dumps(upload_status(profile), ensure_ascii=False, indent=2))
    return 0


def local_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    url = f"http://{config.server.host}:{config.server.port}/health"
    try:
        response = httpx.get(url, timeout=20.0)
    except httpx.HTTPError as exc:
        print(f"[!] 无法连接本地服务 {url}：{exc}")
        print("    先启动：python run.py")
        return 2
    print(f"[+] {url} -> HTTP {response.status_code}")
    try:
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except ValueError:
        print(response.text[:2000])
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器（供 main 与测试复用）。"""
    parser = argparse.ArgumentParser(description="健康检查 / 协议变化检测")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--direct", action="store_true", help="不经过本地服务，直连上游检查")
    parser.add_argument("--write-snapshot", action="store_true", help="把当前指纹写入快照")
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def main() -> None:
    args = parse_args()

    code = asyncio.run(direct_check(args)) if args.direct else local_check(args)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
