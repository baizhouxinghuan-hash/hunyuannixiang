"""差分分析 CLI（任务书 §35）。

用法::

    python tools/diff_captures.py captures/baseline/requests.json
    python tools/diff_captures.py captures/baseline/requests.json --label send_message
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analyzer.diff import build_report, render_markdown  # noqa: E402
from analyzer.endpoint_discovery import load_records  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="抓包差分分析")
    parser.add_argument("path", help="captures/<label>/requests.json 或包含它的目录")
    parser.add_argument("--label", default="send_message", help="要比较的请求类别")
    parser.add_argument("--out", default=None, help="diff_report.md 输出路径（默认写到同目录）")
    args = parser.parse_args()

    target = Path(args.path)
    records_file = target / "requests.json" if target.is_dir() else target
    if not records_file.exists():
        raise SystemExit(f"找不到 {records_file}")

    records = load_records(records_file)
    report = build_report(records, label=args.label)
    markdown = render_markdown(report)

    out_path = Path(args.out) if args.out else records_file.parent / "diff_report.md"
    out_path.write_text(markdown, encoding="utf-8")

    print(f"[+] 记录数：{len(records)}，目标类别：{args.label}，候选数：{report['candidates']}")
    if report.get("note"):
        print(f"[!] {report['note']}")
    for index, pair in enumerate(report.get("pairs") or [], start=1):
        print(f"\n--- 对比 {index} ---")
        print(f"固定字段：{sorted(pair['fixed'].keys())[:12]}")
        print(f"变量字段：{sorted(pair['variable'].keys())[:12]}")
        for name, paths in (pair.get("categories") or {}).items():
            print(f"  {name}: {paths}")
    print(f"\n[+] 报告：{out_path}")


if __name__ == "__main__":
    main()
