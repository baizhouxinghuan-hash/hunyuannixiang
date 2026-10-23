"""接口发现与清单生成（任务书 §4 / §6 / §14）。

把抓包记录聚合成“接口清单”，并按任务书 §14 的表格输出 Markdown。
**状态列严格区分**：confirmed / partially confirmed / inferred / unknown；
抓包得到的候选在人工确认前只写 ``candidate(抓包)``。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 任务书 §14 要求的行（功能 -> 分类标签）
INVENTORY_ROWS: tuple[tuple[str, str], ...] = (
    ("用户信息", "user_info"),
    ("模型列表", "model_list"),
    ("新建会话", "create_conversation"),
    ("发送消息", "send_message"),
    ("流式响应", "stream_response"),
    ("历史会话", "history"),
    ("消息详情", "message_detail"),
    ("停止生成", "stop_generation"),
    ("文件上传", "upload"),
)

STATUS_LABELS = {
    "confirmed": "confirmed",
    "partially_confirmed": "partially confirmed",
    "inferred": "inferred",
    "unknown": "unknown",
}


def build_inventory(records: Sequence[Mapping[str, Any]], *, profile: Any = None) -> dict[str, Any]:
    """把记录聚合成接口清单。"""
    groups: dict[str, dict[str, Any]] = {}
    hosts: set[str] = set()

    for record in records:
        fingerprint = str(record.get("fingerprint") or "unknown")
        hosts.add(str(record.get("host") or ""))
        group = groups.setdefault(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "method": record.get("method"),
                "host": record.get("host"),
                "path": record.get("path"),
                "count": 0,
                "status_codes": set(),
                "labels": set(),
                "reasons": [],
                "confidence": 0.0,
                "stream": False,
                "request_body_keys": sorted((record.get("request_body_json") or {}).keys())
                if isinstance(record.get("request_body_json"), Mapping)
                else [],
                "response_content_type": record.get("response_content_type"),
                "sample_url": record.get("url"),
                "first_seq": record.get("seq"),
                "last_seq": record.get("seq"),
                "response_body_preview": None,
            },
        )
        group["count"] += 1
        if record.get("status") is not None:
            group["status_codes"].add(record.get("status"))
        classification = record.get("classification") or {}
        if classification.get("label"):
            group["labels"].add(classification["label"])
            if classification.get("confidence", 0) > group["confidence"]:
                group["confidence"] = classification.get("confidence", 0)
                group["reasons"] = classification.get("reasons", [])
        group["stream"] = group["stream"] or bool(record.get("stream_frames"))
        group["last_seq"] = record.get("seq")
        if group["response_body_preview"] is None and record.get("response_body"):
            group["response_body_preview"] = str(record["response_body"])[:300]

    serialized: list[dict[str, Any]] = []
    for group in groups.values():
        group["status_codes"] = sorted(x for x in group["status_codes"] if x is not None)
        group["labels"] = sorted(group["labels"])
        group["best_label"] = group["labels"][0] if group["labels"] else "unknown"
        if profile is not None:
            group["profile_status"] = _profile_status(profile, group)
        serialized.append(group)

    serialized.sort(key=lambda item: (-item["count"], str(item["fingerprint"])))
    by_label: dict[str, list[str]] = {}
    for group in serialized:
        by_label.setdefault(group["best_label"], []).append(str(group["fingerprint"]))

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_records": len(records),
        "hosts": sorted(hosts),
        "groups": serialized,
        "by_label": by_label,
        "note": "本清单由抓包自动生成，标签是**候选**；写入 profile 前必须由人工按证据确认。",
    }


def _profile_status(profile: Any, group: Mapping[str, Any]) -> str:
    """把清单条目与 profile 比对，给出当前状态。"""
    path = str(group.get("path") or "")
    for _name, spec in (getattr(profile, "endpoints", {}) or {}).items():
        if spec and getattr(spec, "path", None) == path:
            return STATUS_LABELS.get(spec.status, spec.status)
    return "unknown"


def render_inventory_markdown(inventory: Mapping[str, Any], *, profile: Any = None) -> str:
    """按任务书 §14 的格式渲染 Markdown。"""
    lines: list[str] = [
        "# API 接口清单（api_inventory.md）",
        "",
        f"生成时间：{inventory.get('generated_at')}",
        f"记录总数：{inventory.get('total_records')}",
        f"涉及主机：{', '.join(inventory.get('hosts') or []) or '（无）'}",
        "",
        f"> {inventory.get('note')}",
        "",
        "状态取值：`confirmed` / `partially confirmed` / `inferred` / `unknown` / `candidate(抓包)`",
        "",
        "## 按功能汇总（任务书 §14 表格）",
        "",
        "| 功能 | Method | URL | 状态 | 证据 |",
        "| ---- | ------ | --- | ---- | ---- |",
    ]

    groups: list[Mapping[str, Any]] = list(inventory.get("groups") or [])
    for title, label in INVENTORY_ROWS:
        matched = [g for g in groups if label in (g.get("labels") or [])]
        if not matched:
            lines.append(f"| {title} | - | - | unknown | 抓包中未出现 |")
            continue
        for group in matched[:3]:
            method = group.get("method") or "-"
            url = group.get("path") or group.get("sample_url") or "-"
            status = group.get("profile_status") or "candidate(抓包)"
            evidence = (
                f"#{group.get('first_seq')}~#{group.get('last_seq')}，"
                f"count={group.get('count')}，conf={group.get('confidence')}"
            )
            lines.append(f"| {title} | {method} | `{url}` | {status} | {evidence} |")

    lines.extend(
        [
            "",
            "## 全部指纹（按出现次数排序）",
            "",
            "| 指纹 | 次数 | 状态码 | 标签 | 流式 | 请求体字段 | 响应 Content-Type |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for group in groups:
        lines.append(
            "| `{fp}` | {count} | {codes} | {labels} | {stream} | {keys} | {ct} |".format(
                fp=group.get("fingerprint"),
                count=group.get("count"),
                codes=",".join(str(code) for code in group.get("status_codes") or []),
                labels=",".join(group.get("labels") or []),
                stream="是" if group.get("stream") else "否",
                keys=",".join(group.get("request_body_keys") or []) or "-",
                ct=group.get("response_content_type") or "-",
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_summary_markdown(
    inventory: Mapping[str, Any], analysis: Mapping[str, Any], *, label: str = "", errors: Sequence[str] = ()
) -> str:
    """生成 captures/<label>/summary.md（人看的汇总）。"""
    lines: list[str] = [
        f"# 抓包汇总 summary.md {('（' + label + '）') if label else ''}",
        "",
        f"- 生成时间：{inventory.get('generated_at')}",
        f"- 记录总数：{inventory.get('total_records')}",
        f"- 主机：{', '.join(inventory.get('hosts') or []) or '（无）'}",
        "",
        "## 抓取到的流式响应",
        "",
    ]
    streams = (analysis.get("streams") or {}).get("streams") or []
    if streams:
        lines.append("| URL | 判定格式 | 置信度 | 事件名 | 有 DONE |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in streams:
            lines.append(
                f"| `{item.get('url')}` | {item.get('format')} | {item.get('confidence')} | "
                f"{','.join(item.get('event_names') or []) or '-'} | {'是' if item.get('has_done_marker') else '否'} |"
            )
    else:
        lines.append("（未捕获到流式帧；请确认抓包期间真的点过“发送”）")

    lines.extend(["", "## WebSocket", ""])
    websockets = (analysis.get("streams") or {}).get("websockets") or []
    if websockets:
        for item in websockets:
            lines.append(f"- `{item.get('url')}` 帧数={item.get('frames')} 方向={item.get('directions')}")
    else:
        lines.append("（未捕获到 WebSocket）")

    lines.extend(["", "## 差分分析（diff_report.md）", ""])
    diff = analysis.get("diff") or {}
    lines.append(f"- 候选请求数：{diff.get('candidates')}")
    if diff.get("note"):
        lines.append(f"- 说明：{diff['note']}")

    if errors:
        lines.extend(["", "## 抓包过程中的错误", ""])
        lines.extend(f"- {error}" for error in errors)
    lines.append("")
    return "\n".join(lines)


def write_inventory(
    inventory: Mapping[str, Any],
    json_path: str | Path,
    md_path: str | Path | None = None,
    *,
    profile: Any = None,
) -> dict[str, str]:
    """写出 api_inventory.json（以及可选的 .md）。"""
    json_target = Path(json_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    written = {"json": str(json_target)}
    if md_path:
        md_target = Path(md_path)
        md_target.parent.mkdir(parents=True, exist_ok=True)
        md_target.write_text(render_inventory_markdown(inventory, profile=profile), encoding="utf-8")
        written["markdown"] = str(md_target)
    return written


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """读取 captures/requests.json。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, Mapping) and "records" in data:
        return list(data["records"])
    return list(data)
