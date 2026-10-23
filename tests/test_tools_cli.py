"""CLI 冒烟测试（工具链可用性）。

只运行**不触网**的命令；真实抓包/上游访问不在这里做。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import ROOT
from tests.fixtures.synthetic import build_synthetic_dicts


def _run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
    )


def test_analyze_bundle_help():
    result = _run("tools/analyze_bundle.py", "--help")
    assert result.returncode == 0, result.stderr
    assert "--force" in result.stdout


def test_capture_help():
    result = _run("tools/capture.py", "--help")
    assert result.returncode == 0, result.stderr
    assert "--interactive" in result.stdout or "--non-interactive" in result.stdout


def test_import_session_reports_missing_state():
    result = _run("tools/import_session.py", "--storage-state", "does-not-exist.json")
    assert result.returncode == 2
    assert "capture.py" in result.stdout


def test_diff_captures_cli(tmp_path: Path):
    capture_dir = tmp_path / "captures" / "unit"
    capture_dir.mkdir(parents=True)
    records = build_synthetic_dicts()
    (capture_dir / "requests.json").write_text(
        json.dumps({"meta": {"label": "unit"}, "records": records}, ensure_ascii=False), encoding="utf-8"
    )

    result = _run("tools/diff_captures.py", str(capture_dir))
    assert result.returncode == 0, result.stderr
    assert "候选数：2" in result.stdout
    report = (capture_dir / "diff_report.md").read_text(encoding="utf-8")
    assert "变量字段" in report
    assert "$.prompt" in report


def test_replay_cli_dry_run(tmp_path: Path):
    capture_dir = tmp_path / "captures" / "unit"
    capture_dir.mkdir(parents=True)
    records = build_synthetic_dicts()
    (capture_dir / "requests.json").write_text(
        json.dumps({"meta": {"label": "unit"}, "records": records}, ensure_ascii=False), encoding="utf-8"
    )

    result = _run("tools/replay.py", str(capture_dir / "requests.json"), "--seq", "3")
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert "SECRET-COOKIE-VALUE-123" not in result.stdout
    assert "****" in result.stdout


def test_replay_cli_requires_matching_record(tmp_path: Path):
    capture_dir = tmp_path / "captures" / "unit"
    capture_dir.mkdir(parents=True)
    (capture_dir / "requests.json").write_text(json.dumps({"records": []}), encoding="utf-8")
    result = _run("tools/replay.py", str(capture_dir / "requests.json"))
    assert result.returncode != 0
    assert "没有记录" in result.stdout or "没有记录" in result.stderr


def test_healthcheck_reports_unreachable_server():
    result = _run("tools/healthcheck.py", "--config", "config.yaml")
    # 本地服务通常没启动：应给出可读提示而不是堆栈
    assert "无法连接本地服务" in result.stdout or result.returncode == 0
