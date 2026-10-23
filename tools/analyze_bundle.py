"""公开前端 bundle 分析（把 docs/findings.md 的 E1-E10 固化成可复现脚本）。

用法::

    python tools/analyze_bundle.py                 # 分析并写出 frontend/
    python tools/analyze_bundle.py --force         # 忽略缓存重新下载

只访问公开静态资源（页面 HTML + CDN 上的 JS/CSS），不使用任何凭证。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PORTAL = "https://aistudio.tencent.com/"
#: 关键 bundle（页面 HTML 会动态解析，这里是缺省清单）
KNOWN_BUNDLES = (
    "hy-web__e62837fa9ff77f96314a.js",
    "index__70332c7932348066f545.js",
    "6473__39850b1ae564af0fd214.js",
    "utils__731e71f93fbdc6dcbbb9.js",
)

KEYWORDS = (
    "text/event-stream",
    "EventSource",
    "WebSocket",
    "getReader",
    "ReadableStream",
    "TextDecoder",
    "onprogress",
    "ndjson",
    'credentials:"include"',
    "Authorization",
    "signature",
    "nonce",
    "timestamp",
    "TmpSecretId",
    "new-portal/chat",
    "new-portal/stop",
)

HOST_RE = re.compile(r"https?://[a-zA-Z0-9_.\-]+")
API_PATH_RE = re.compile(r"/api/[a-zA-Z0-9_\-/\.]{2,60}")
SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="([^"]+)"')
LINK_HREF_RE = re.compile(r'<link[^>]+href="([^"]+)"')


def fetch(client: httpx.Client, url: str) -> str:
    response = client.get(url, timeout=60.0)
    response.raise_for_status()
    return response.text


def collect_assets(html: str) -> list[str]:
    assets = SCRIPT_SRC_RE.findall(html) + LINK_HREF_RE.findall(html)
    return list(dict.fromkeys(assets))


def analyze_js(text: str) -> dict[str, Any]:
    hosts = sorted(set(HOST_RE.findall(text)))
    paths = sorted(set(API_PATH_RE.findall(text)))
    return {
        "length": len(text),
        "hosts": hosts,
        "api_paths": paths,
        "api_path_count": len(paths),
        "keywords": {word: text.count(word) for word in KEYWORDS},
        "has_source_mapping_url": "sourceMappingURL" in text,
    }


def render_summary(report: dict[str, Any]) -> str:
    lines = [
        "# 前端 bundle 分析结果（可复现）",
        "",
        f"- 入口：{report['entry_url']}",
        f"- 标题：{report.get('title')}",
        f"- /api 路径总数：{report.get('api_path_total')}",
        f"- 是否出现 api.hunyuan.tencent.com：**{report.get('has_api_hunyuan_host')}**",
        "",
        "## bundle 概览",
        "",
        "| bundle | 大小 | /api 路径数 | 值得注意的关键字 |",
        "| --- | --- | --- | --- |",
    ]
    notable_keys = (
        "signature", "nonce", "timestamp", "text/event-stream", "EventSource",
        "WebSocket", "TmpSecretId", "getReader", "ReadableStream",
    )
    for name, info in sorted((report.get("bundles") or {}).items()):
        if "error" in info:
            lines.append(f"| {name} | - | - | 下载失败：{info['error']} |")
            continue
        keywords = info.get("keywords") or {}
        notable = {k: v for k, v in keywords.items() if v and k in notable_keys}
        lines.append(f"| {name} | {info.get('length')} | {info.get('api_path_count')} | {notable or '-'} |")

    lines.extend(["", "## 每个 bundle 的关键字计数", ""])
    for name, info in sorted((report.get("bundles") or {}).items()):
        if "error" in info:
            continue
        lines.append(f"### {name}")
        lines.append("")
        for word, count in (info.get("keywords") or {}).items():
            lines.append(f"- `{word}`：{count}")
        lines.append("")

    lines.extend(["", "## source map 检查（任务书 §13）", ""])
    for name, status in (report.get("source_maps") or {}).items():
        lines.append(f"- `{name}.map` -> {status}")

    lines.extend(["", "## 全部 /api 路径", "", "```text"])
    lines.extend(report.get("all_api_paths") or [])
    lines.extend(["```", "", "## 全部主机", "", "```text"])
    lines.extend(report.get("all_hosts") or [])
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="公开前端 bundle 分析")
    parser.add_argument("--force", action="store_true", help="忽略本地缓存，重新下载")
    parser.add_argument("--out", default=str(ROOT / "frontend"), help="输出目录")
    args = parser.parse_args()

    out_dir = Path(args.out)
    bundle_dir = out_dir / "bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {"entry_url": PORTAL, "assets": [], "bundles": {}, "source_maps": {}}

    with httpx.Client(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        html = fetch(client, PORTAL)
        (out_dir / "index.html").write_text(html, encoding="utf-8")
        title_match = re.search(r"<title>(.*?)</title>", html)
        report["title"] = title_match.group(1) if title_match else None
        assets = collect_assets(html)
        report["assets"] = assets

        js_assets = [asset for asset in assets if asset.endswith(".js")]
        targets = js_assets or [
            f"https://cdn-portal.hunyuan.tencent.com/public/{name}" for name in KNOWN_BUNDLES
        ]

        for asset in targets:
            name = asset.split("/")[-1]
            local = bundle_dir / name
            try:
                if not local.exists() or args.force:
                    local.write_text(fetch(client, asset), encoding="utf-8")
                info = analyze_js(local.read_text(encoding="utf-8"))
            except (httpx.HTTPError, OSError) as exc:
                report["bundles"][name] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            info["local_path"] = str(local)
            report["bundles"][name] = info
            try:
                head = client.head(asset + ".map", timeout=20.0)
                report["source_maps"][name] = head.status_code
            except httpx.HTTPError as exc:
                report["source_maps"][name] = f"error:{type(exc).__name__}"

    all_hosts: set[str] = set()
    all_paths: set[str] = set()
    for info in report["bundles"].values():
        all_hosts.update(info.get("hosts") or [])
        all_paths.update(info.get("api_paths") or [])
    report["all_hosts"] = sorted(all_hosts)
    report["all_api_paths"] = sorted(all_paths)
    report["api_path_total"] = len(all_paths)
    report["has_api_hunyuan_host"] = any("api.hunyuan.tencent.com" in host for host in all_hosts)

    (out_dir / "endpoints_raw.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(render_summary(report), encoding="utf-8")

    print(f"[+] 标题：{report.get('title')}")
    print(f"[+] bundle 数：{len(report['bundles'])}，/api 路径总数：{report['api_path_total']}")
    print(f"[+] 主机数：{len(report['all_hosts'])}")
    print(f"[+] 是否存在 api.hunyuan.tencent.com：{report['has_api_hunyuan_host']}")
    print(f"[+] 结果：{out_dir / 'endpoints_raw.json'}")


if __name__ == "__main__":
    main()
