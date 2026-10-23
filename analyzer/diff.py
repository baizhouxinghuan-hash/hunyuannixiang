"""差分分析（任务书 §35）。

输入同类请求的多次抓包记录，输出：
* 固定字段 / 变量字段；
* 变量字段语义归类：时间字段、随机字段、会话字段、消息字段、签名字段；
* Markdown 报告（``captures/<label>/diff_report.md``）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

TIME_KEYS = ("time", "ts", "timestamp", "date", "created_at", "starttime", "expire")
SESSION_KEYS = ("conversation_id", "chat_id", "chatid", "session_id", "sessionid", "chat_record_id")
MESSAGE_KEYS = ("message", "messages", "prompt", "query", "content", "text", "input", "question")
SIGN_KEYS = ("sign", "signature", "nonce", "token", "hash", "checksum", "digest", "traceid", "trace_id")

_RANDOM_RE = re.compile(r"^[A-Za-z0-9_\-+/=]{16,}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX_RE = re.compile(r"^[0-9a-f]{16,}$", re.I)


@dataclass
class DiffEntry:
    path: str
    kind: str  # same | changed | added | removed
    left: Any = None
    right: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "left": self.left, "right": self.right}


def diff_json(left: Any, right: Any, path: str = "$") -> list[DiffEntry]:
    """递归比较两个 JSON 结构。"""
    entries: list[DiffEntry] = []
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        for key in sorted(set(left.keys()) | set(right.keys())):
            child = f"{path}.{key}"
            if key not in left:
                entries.append(DiffEntry(child, "added", None, right[key]))
            elif key not in right:
                entries.append(DiffEntry(child, "removed", left[key], None))
            else:
                entries.extend(diff_json(left[key], right[key], child))
        return entries
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            entries.append(DiffEntry(f"{path}.length", "changed", len(left), len(right)))
        for index, (a_item, b_item) in enumerate(zip(left, right)):
            entries.extend(diff_json(a_item, b_item, f"{path}[{index}]"))
        return entries
    if left != right:
        entries.append(DiffEntry(path, "changed", left, right))
    else:
        entries.append(DiffEntry(path, "same", left, right))
    return entries


def _looks_like_time(value: Any, key: str = "") -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        text = str(int(value))
        now_ms = int(datetime.now().timestamp() * 1000)
        if len(text) in (10, 13) and abs(int(text) - now_ms) < 10 * 365 * 24 * 3600 * 1000:
            return True
    if isinstance(value, str):
        if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            return True
        if value.isdigit() and len(value) in (10, 13):
            return True
    return any(token in key.lower() for token in TIME_KEYS)


def _looks_like_random(value: Any) -> bool:
    if not isinstance(value, str) or len(value) < 16:
        return False
    if _UUID_RE.match(value) or _HEX_RE.match(value):
        return True
    if not _RANDOM_RE.match(value):
        return False
    digits = sum(char.isdigit() for char in value)
    letters = sum(char.isalpha() for char in value)
    return digits >= 4 and letters >= 4


def categorize(entries: Sequence[DiffEntry]) -> dict[str, list[str]]:
    """把变量字段归类（任务书 §35 的分类）。"""
    categories: dict[str, list[str]] = {
        "会话字段": [],
        "消息字段": [],
        "签名字段": [],
        "时间字段": [],
        "随机字段": [],
        "其他变量字段": [],
    }
    for entry in entries:
        if entry.kind == "same":
            continue
        key = entry.path.rstrip("]").split("[")[0].split(".")[-1].lower()
        if any(token in key for token in SIGN_KEYS):
            categories["签名字段"].append(entry.path)
        elif any(token in key for token in SESSION_KEYS):
            categories["会话字段"].append(entry.path)
        elif any(token in key for token in MESSAGE_KEYS):
            categories["消息字段"].append(entry.path)
        elif _looks_like_time(entry.right if entry.right is not None else entry.left, key):
            categories["时间字段"].append(entry.path)
        elif _looks_like_random(entry.right if entry.right is not None else entry.left):
            categories["随机字段"].append(entry.path)
        else:
            categories["其他变量字段"].append(entry.path)
    return {name: sorted(set(values)) for name, values in categories.items() if values}


def pick_diff_candidates(records: Sequence[Mapping[str, Any]], *, label: str = "send_message") -> list[Mapping[str, Any]]:
    """挑出同类请求（默认“发送消息”候选）。"""
    return [record for record in records if (record.get("classification") or {}).get("label") == label]


def build_report(records: Sequence[Mapping[str, Any]], *, label: str = "send_message") -> dict[str, Any]:
    """对同类请求两两对比，生成差分报告数据。"""
    candidates = pick_diff_candidates(records, label=label)
    report: dict[str, Any] = {"label": label, "candidates": len(candidates), "pairs": [], "note": None}
    if len(candidates) < 2:
        report["note"] = (
            f"同类请求不足 2 条（找到 {len(candidates)} 条），无法差分；"
            "请在两次发送中使用不同内容（例如先发“你好”，再发“今天天气怎么样”）。"
        )
        return report

    base = candidates[0]
    for other in candidates[1:]:
        entries = diff_json(base.get("request_body_json"), other.get("request_body_json"))
        report["pairs"].append(
            {
                "left": {
                    "seq": base.get("seq"),
                    "ts": base.get("ts"),
                    "url": base.get("url"),
                    "fingerprint": base.get("fingerprint"),
                },
                "right": {
                    "seq": other.get("seq"),
                    "ts": other.get("ts"),
                    "url": other.get("url"),
                    "fingerprint": other.get("fingerprint"),
                },
                "fixed": {entry.path: entry.left for entry in entries if entry.kind == "same"},
                "variable": {
                    entry.path: {"left": entry.left, "right": entry.right}
                    for entry in entries
                    if entry.kind == "changed"
                },
                "added": [entry.path for entry in entries if entry.kind == "added"],
                "removed": [entry.path for entry in entries if entry.kind == "removed"],
                "categories": categorize(entries),
            }
        )
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    """渲染 ``diff_report.md``。"""
    lines: list[str] = ["# 差分分析报告（diff_report.md）", ""]
    lines.append(f"- 目标标签：`{report.get('label')}`")
    lines.append(f"- 候选请求数：{report.get('candidates')}")
    if report.get("note"):
        lines.append(f"- 说明：{report['note']}")
    lines.append("")

    for index, pair in enumerate(report.get("pairs") or [], start=1):
        left, right = pair["left"], pair["right"]
        lines.append(f"## 对比 {index}: #{left.get('seq')} vs #{right.get('seq')}")
        lines.append("")
        lines.append(f"- 左：`{left.get('url')}`")
        lines.append(f"- 右：`{right.get('url')}`")
        lines.append(f"- 指纹：`{left.get('fingerprint')}`")
        lines.append("")
        lines.append("### 固定字段")
        lines.append("")
        if pair["fixed"]:
            lines.append("| 路径 | 值 |")
            lines.append("| --- | --- |")
            for path, value in list(pair["fixed"].items())[:50]:
                lines.append(f"| `{path}` | `{_short(value)}` |")
        else:
            lines.append("（无）")
        lines.append("")
        lines.append("### 变量字段")
        lines.append("")
        if pair["variable"]:
            lines.append("| 路径 | 左值 | 右值 |")
            lines.append("| --- | --- | --- |")
            for path, values in list(pair["variable"].items())[:80]:
                lines.append(f"| `{path}` | `{_short(values['left'])}` | `{_short(values['right'])}` |")
        else:
            lines.append("（无）")
        lines.append("")
        lines.append("### 变量字段分类")
        lines.append("")
        categories = pair.get("categories") or {}
        if categories:
            for name, paths in categories.items():
                lines.append(f"- **{name}**：{', '.join(f'`{p}`' for p in paths)}")
        else:
            lines.append("（无）")
        if pair.get("added"):
            lines.append("")
            lines.append(f"- 仅在右侧出现：{', '.join(f'`{p}`' for p in pair['added'])}")
        if pair.get("removed"):
            lines.append(f"- 仅在左侧出现：{', '.join(f'`{p}`' for p in pair['removed'])}")
        lines.append("")
    return "\n".join(lines)


def _short(value: Any, limit: int = 80) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"
