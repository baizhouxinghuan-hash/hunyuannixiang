"""分析结果统一入口（任务书 §38 的 ``analyzer/parser.py``）。

把 fingerprint / classifier / response_parser / endpoint_discovery / diff
组合成“给一个 captures 目录，产出一套分析结果”的函数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from analyzer.diff import build_report, render_markdown as render_diff_markdown
from analyzer.endpoint_discovery import build_inventory, load_records, render_inventory_markdown
from analyzer.fingerprint import fingerprint, fingerprint_hash, fingerprint_struct
from analyzer.request_classifier import classify, classify_records
from analyzer.response_parser import analyze_records, detect

__all__ = [
    "analyze_records",
    "analyze_capture_dir",
    "build_inventory",
    "build_report",
    "classify",
    "classify_records",
    "detect",
    "fingerprint",
    "fingerprint_hash",
    "fingerprint_struct",
    "load_records",
    "render_diff_markdown",
    "render_inventory_markdown",
]


def analyze_capture_dir(path: str | Path, *, profile: Any = None) -> dict[str, Any]:
    """对 captures 目录做一次完整分析。"""
    target = Path(path)
    records_file = target / "requests.json" if target.is_dir() else target
    records = load_records(records_file)
    if not any(record.get("classification") for record in records):
        records = classify_records(records)
    return {
        "inventory": build_inventory(records, profile=profile),
        "streams": analyze_records(records),
        "diff": build_report(records),
    }
