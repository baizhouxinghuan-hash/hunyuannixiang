"""抓包会话编排（任务书 §34 ``tools/capture.py`` 的实现）。

流程
----
1. 用持久化 Profile 启动有头 Chromium（登录由用户手动完成）；
2. 双通道记录网络（CDP + 页面注入）；
3. 期间周期性把页面内记录取回并合并；
4. 结束后导出 storage_state（进 ``secrets/``）、写出
   ``captures/<label>/{requests.json,responses.json,summary.md,diff_report.md,api_inventory.json,session.har}``。

原始凭证只落地到 ``secrets/``；``captures/`` 内容全部经过脱敏。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from analyzer.browser import DEFAULT_URL, BrowserSession
from analyzer.diff import build_report, render_markdown as render_diff_markdown
from analyzer.endpoint_discovery import build_inventory, render_summary_markdown, write_inventory
from analyzer.network_capture import NetworkRecorder
from analyzer.response_parser import analyze_records

#: 自动输入模式使用的选择器（**未经验证的猜测**，失败请改用 --interactive）
GUESSED_INPUT_SELECTOR = "textarea, div[contenteditable='true'], [contenteditable='true']"
GUESSED_SEND_SELECTOR = "button:has-text('发送'), button[type='submit']"


@dataclass
class CaptureOptions:
    label: str = "baseline"
    url: str = DEFAULT_URL
    out_dir: Path = Path("captures")
    secrets_dir: Path = Path("secrets")
    har: bool = True
    headless: bool = False
    duration: float | None = None
    interactive: bool = True
    prompts: list[str] = field(default_factory=list)
    input_selector: str | None = None
    send_selector: str | None = None
    capture_all: bool = False
    poll_interval: float = 2.0
    goto: bool = True
    keep_open: bool = False

    @property
    def capture_dir(self) -> Path:
        return Path(self.out_dir) / self.label


INSTRUCTIONS = """
================= 请在弹出的浏览器里手动完成以下操作 =================
 1) 自己完成登录（扫码/短信由你本人完成，本工具不参与登录流程）
 2) 新建会话，发送：你好，请简单介绍一下自己。
 3) 等回答开始输出后，点一次【停止生成】
 4) 再点一次【重新生成】
 5) 再发送一条：今天天气怎么样
 6) 如果页面支持，上传一张图片并发送一条围绕图片的问题
 7) 完成后回到本终端按 Enter 结束抓包
（如果不按 Enter，也可以用 --duration 秒数自动结束）
====================================================================
"""


async def _poll(recorder: NetworkRecorder, session: BrowserSession, interval: float) -> None:
    """周期性把页面内记录合并进来。"""
    while True:
        await asyncio.sleep(interval)
        try:
            page = await session.page()
            await recorder.collect_from_page(page)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception as exc:  # pragma: no cover
            recorder.errors.append(f"poll:{type(exc).__name__}:{exc}")


async def _wait_for_finish(options: CaptureOptions) -> str:
    if options.duration:
        await asyncio.sleep(options.duration)
        return "duration"
    if options.interactive:
        await asyncio.to_thread(input, "\n>>> 完成操作后按 Enter 结束抓包 ... ")
        return "user"
    return "immediate"


async def _auto_chat(session: BrowserSession, options: CaptureOptions) -> list[str]:
    """半自动发送（选择器是猜测值，失败请改用 --interactive）。"""
    notes: list[str] = []
    page = await session.page()
    input_selector = options.input_selector or GUESSED_INPUT_SELECTOR
    send_selector = options.send_selector or GUESSED_SEND_SELECTOR
    for index, prompt in enumerate(options.prompts, start=1):
        try:
            await page.wait_for_selector(input_selector, timeout=30_000)
            await page.click(input_selector, timeout=10_000)
            await page.fill(input_selector, prompt)
        except Exception as exc:
            notes.append(f"第 {index} 条消息：输入框定位失败（{type(exc).__name__}），请改用 --interactive")
            break
        sent = False
        try:
            await page.click(send_selector, timeout=5_000)
            sent = True
        except Exception:
            pass
        if not sent:
            try:
                await page.keyboard.press("Enter")
                sent = True
            except Exception as exc:
                notes.append(f"第 {index} 条消息：发送失败（{type(exc).__name__}）")
                break
        notes.append(f"第 {index} 条消息已发送：{prompt[:40]}")
        await asyncio.sleep(2.0)
    return notes


def write_artifacts(
    recorder: NetworkRecorder,
    options: CaptureOptions,
    *,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, str]:
    """落盘抓包与分析产物。"""
    out = options.capture_dir
    out.mkdir(parents=True, exist_ok=True)

    recorder.finalize()
    dicts = recorder.to_dicts(redact=True)

    meta: dict[str, Any] = {
        "label": options.label,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "url": options.url,
        "record_count": len(dicts),
        "capture_errors": list(recorder.errors),
        "note": "所有凭证字段已脱敏（****）；原始值只在 secrets/ 目录中。",
    }
    if extra_meta:
        meta.update(extra_meta)

    requests_path = out / "requests.json"
    requests_path.write_text(
        json.dumps({"meta": meta, "records": dicts}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    responses = [
        {
            "seq": record.get("seq"),
            "method": record.get("method"),
            "url": record.get("url"),
            "status": record.get("status"),
            "content_type": record.get("response_content_type"),
            "body_preview": (record.get("response_body") or "")[:2000],
            "stream_preview": "".join(record.get("stream_frames") or [])[:4000],
        }
        for record in dicts
    ]
    responses_path = out / "responses.json"
    responses_path.write_text(json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8")

    inventory = build_inventory(dicts)
    analysis = {"streams": analyze_records(dicts), "diff": build_report(dicts)}

    summary_path = out / "summary.md"
    summary_path.write_text(
        render_summary_markdown(inventory, analysis, label=options.label, errors=recorder.errors),
        encoding="utf-8",
    )
    diff_path = out / "diff_report.md"
    diff_path.write_text(render_diff_markdown(analysis["diff"]), encoding="utf-8")

    written_inventory = write_inventory(inventory, out / "api_inventory.json")
    analysis_path = out / "analysis.json"
    analysis_path.write_text(
        json.dumps({"inventory": inventory, **analysis}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "requests": str(requests_path),
        "responses": str(responses_path),
        "summary": str(summary_path),
        "diff_report": str(diff_path),
        "analysis": str(analysis_path),
        **written_inventory,
    }


async def run_capture(options: CaptureOptions) -> dict[str, Any]:
    """执行一次抓包会话。"""
    secrets_dir = Path(options.secrets_dir)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    har_path = options.capture_dir / "session.har" if options.har else None

    session = BrowserSession(
        base_url=options.url,
        profile_dir=secrets_dir / "browser_profile",
        har_path=har_path,
        headless=options.headless,
        capture_all=options.capture_all,
    )
    result: dict[str, Any] = {"label": options.label, "notes": [], "artifacts": {}}
    try:
        await session.start()
        if options.goto:
            await session.goto_home()
        if options.interactive:
            print(INSTRUCTIONS, flush=True)
        if options.prompts and not options.interactive:
            result["notes"].extend(await _auto_chat(session, options))

        poller = asyncio.create_task(_poll(session.recorder, session, options.poll_interval))
        try:
            result["ended_by"] = await _wait_for_finish(options)
        finally:
            poller.cancel()
            try:
                await poller
            except asyncio.CancelledError:
                pass

        page = await session.page()
        await session.recorder.collect_from_page(page)

        storage_state_path = await session.export_storage_state(secrets_dir / "storage_state.json")
        snapshot = await session.storage_snapshot()
        snapshot_path = secrets_dir / f"storage_snapshot.{options.label}.json"
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        session_summary = await session.session_summary()
        result["notes"].append(f"storage_state 已写入 {storage_state_path}")
        result["notes"].append(f"storage 快照已写入 {snapshot_path}")

        result["session"] = session_summary
        result["artifacts"] = write_artifacts(
            session.recorder,
            options,
            extra_meta={"ended_by": result.get("ended_by"), "session": session_summary},
        )
        result["summary"] = session.recorder.summary()
    finally:
        if not options.keep_open:
            await session.close()
    return result
