"""本地工具桥接：OpenAI ``tools`` -> prompt 说明 -> 标准 ``tool_calls``。

背景（抓包确认）
----------------
上游网页端 chat 接口的请求体**只有一个 ``prompt`` 字符串**，没有 ``role`` / ``tools`` /
``tool_calls`` 之类的字段，因此无法原生透传 OpenAI 的工具调用协议。

本模块在**本地**做一层桥接，让客户端（AstrBot / Cline 等）看到标准行为：

1. 请求侧：把 ``tools`` 的 name / description / parameters(JSON Schema) 渲染成一段
   "工具说明书"追加到 prompt 末尾，并要求模型**只输出一个 JSON**；
2. 响应侧：把模型输出的 JSON 解析成标准 OpenAI ``tool_calls``（``arguments`` 为 JSON 字符串）；
3. 解析失败 -> 原样当普通文本返回（**绝不因为桥接失败而丢回答**）。

⚠️ 这是**本代理的本地适配策略**（inferred），不代表上游协议；一旦抓到上游原生工具协议，
应优先改为原生透传（见 ``protocol/current/profile.yaml`` 的 ``features.tool_calls``）。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Iterable, Mapping

INSTRUCTION_HEADER = "【工具调用说明】"

#: 模型应当输出的形状（同时也是我们解析时优先识别的形状）
OUTPUT_EXAMPLE = '{"tool_calls":[{"name":"<工具名>","arguments":{<参数>}}]}'

_FENCED_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.S)


def function_specs(tools: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """把 OpenAI ``tools`` 提取成 ``[{name, description, parameters}]``（容忍两种写法）。"""
    specs: list[dict[str, Any]] = []
    for item in tools or []:
        if not isinstance(item, Mapping):
            continue
        function = item.get("function") if isinstance(item.get("function"), Mapping) else item
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        spec: dict[str, Any] = {
            "name": name,
            "description": str(function.get("description") or "").strip(),
        }
        parameters = function.get("parameters")
        if isinstance(parameters, Mapping):
            spec["parameters"] = dict(parameters)
        specs.append(spec)
    return specs


def tool_choice_disables_tools(tool_choice: Any) -> bool:
    """``tool_choice="none"`` 表示禁止调用工具（照 OpenAI 语义）。"""
    return str(tool_choice or "").strip().lower() == "none"


def forced_tool_name(tool_choice: Any) -> str:
    """``tool_choice={"type":"function","function":{"name":X}}`` / ``{"name":X}`` -> X。"""
    if isinstance(tool_choice, Mapping):
        function = tool_choice.get("function")
        if isinstance(function, Mapping) and function.get("name"):
            return str(function["name"])
        if tool_choice.get("name"):
            return str(tool_choice["name"])
    return ""


def render_tools_instructions(
    tools: Iterable[Mapping[str, Any]] | None, tool_choice: Any = None
) -> str | None:
    """把工具定义渲染成一段追加到 prompt 末尾的说明文本；没有可用工具时返回 None。"""
    specs = function_specs(tools)
    if not specs:
        return None
    lines = [
        INSTRUCTION_HEADER,
        "你可以使用下列工具。**需要调用工具时，只输出一个 JSON 对象**，",
        "不要输出任何解释、前后缀、Markdown 代码块或额外文字：",
        OUTPUT_EXAMPLE,
        "不需要调用工具时，就用自然语言正常回答。",
        "",
        "可用工具：",
    ]
    for spec in specs:
        lines.append(f"- {spec['name']}：{spec['description'] or '（无描述）'}")
        parameters = spec.get("parameters")
        if parameters:
            lines.append(f"  参数（JSON Schema）：{json.dumps(parameters, ensure_ascii=False)}")
    forced = forced_tool_name(tool_choice)
    if forced:
        lines += ["", f"本次**必须**调用工具 {forced}，不要直接回答。"]
    elif str(tool_choice or "").strip().lower() == "required":
        lines += ["", "本次**必须**调用其中某个工具，不要直接回答。"]
    return "\n".join(lines)


def build_openai_tool_call(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """构造标准 OpenAI ``tool_calls`` 元素（``arguments`` 必须是 JSON **字符串**）。"""
    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {
            "name": str(name),
            "arguments": json.dumps(dict(arguments), ensure_ascii=False),
        },
    }


def parse_tool_calls(
    text: str, *, allowed_names: Iterable[str] | None = None
) -> tuple[list[dict[str, Any]], str]:
    """从模型输出里解析工具调用。

    * ``allowed_names`` 非空时，只接受**请求里声明过的工具名**（防止模型复述提示词里的
      示例 JSON 造成"幻觉调用"）；
    * 返回 ``(tool_calls, remaining_text)``：解析不到时 ``tool_calls == []`` 且
      ``remaining_text`` 为原文（调用方按普通文本处理）。
    """
    raw = text or ""
    payload, start, end = _find_json_payload(raw)
    if payload is None:
        return [], raw
    calls = _calls_from_payload(payload)
    if not calls:
        return [], raw
    if allowed_names is not None:
        allowed = {str(name) for name in allowed_names}
        calls = [call for call in calls if call["function"]["name"] in allowed]
        if not calls:
            return [], raw
    remaining = (raw[:start] + raw[end:]).strip()
    return calls, remaining


def _find_json_payload(text: str) -> tuple[Any, int, int]:
    """找出第一段可解析的 JSON。

    顺序：整段文本（若以 ``{`` / ``[`` 开头）-> ```json 代码块 -> 花括号配对扫描。
    """
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            payload = json.loads(stripped)
            start = text.index(stripped[0])
            return payload, start, start + len(stripped)
        except ValueError:
            pass
    candidates: list[tuple[str, int, int]] = []
    fenced = _FENCED_RE.search(text)
    if fenced:
        candidates.append((fenced.group(1).strip(), fenced.start(), fenced.end()))
    candidates.extend(_brace_spans(text))
    for candidate, start, end in candidates:
        try:
            return json.loads(candidate), start, end
        except ValueError:
            continue
    return None, -1, -1


def _brace_spans(text: str) -> list[tuple[str, int, int]]:
    """按花括号配对扫描出所有顶层 ``{...}`` 片段（跳过字符串内的括号）。"""
    spans: list[tuple[str, int, int]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    spans.append((text[start : index + 1], start, index + 1))
                    start = -1
    return spans


def _calls_from_payload(payload: Any) -> list[dict[str, Any]]:
    items: list[Any] = []
    if isinstance(payload, Mapping):
        raw_calls = payload.get("tool_calls")
        if isinstance(raw_calls, list):
            items = list(raw_calls)
        elif payload.get("name"):
            items = [payload]
    elif isinstance(payload, list):
        items = list(payload)
    calls: list[dict[str, Any]] = []
    for item in items:
        call = _call_from_item(item)
        if call:
            calls.append(call)
    return calls


def _call_from_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    function = item.get("function") if isinstance(item.get("function"), Mapping) else item
    name = str(function.get("name") or "").strip()
    if not name:
        return None
    arguments = function.get("arguments")
    if arguments is None:
        arguments = item.get("arguments")
    if isinstance(arguments, str):
        stripped = arguments.strip()
        if not stripped:
            arguments = {}
        else:
            try:
                arguments = json.loads(stripped)
            except ValueError:
                arguments = {"_raw": stripped}
    if not isinstance(arguments, Mapping):
        arguments = {"_value": arguments}
    return build_openai_tool_call(name, dict(arguments))
