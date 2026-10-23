"""请求构造：把 profile 里的 body 模板渲染成真实请求体。

这里**不含任何硬编码字段名** —— 字段名全部来自
``protocol/current/profile.yaml`` 的 ``request_schema.body_template``。
模板未确认时抛 :class:`ProtocolUnknownError`。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.protocol.profile import Profile
from app.upstream.errors import ProtocolUnknownError

_PLACEHOLDER_RE = re.compile(r"^\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")

#: 渲染模板时可用的变量名（用于错误提示）
KNOWN_VARIABLES = (
    "conversation_id",
    "model",
    "prompt",
    "stream",
    "messages",
    "message_id",
    "effort",
    "messages_text",
)


def render_template(template: Any, variables: Mapping[str, Any], *, path: str = "$") -> Any:
    """递归渲染模板。

    * 整个字符串就是一个 ``{var}`` 占位符时，直接返回原始类型的值；
    * 其他字符串按字面量处理（不做半替换，避免产生歧义）；
    * 引用了未提供的变量 -> ``ProtocolUnknownError``。
    """
    if isinstance(template, str):
        match = _PLACEHOLDER_RE.match(template.strip())
        if not match:
            return template
        name = match.group(1)
        if name not in variables:
            raise ProtocolUnknownError(
                f"request_schema.body_template{path}",
                f"模板引用了变量 {{{name}}}，但调用方未提供；"
                f"当前可用变量：{sorted(variables)}，常见变量：{list(KNOWN_VARIABLES)}",
            )
        return variables[name]
    if isinstance(template, Mapping):
        return {
            key: render_template(value, variables, path=f"{path}.{key}") for key, value in template.items()
        }
    if isinstance(template, (list, tuple)):
        return [render_template(item, variables, path=f"{path}[{i}]") for i, item in enumerate(template)]
    return template


def format_path(path: str, **kwargs: Any) -> str:
    """把 endpoint 路径里的占位符换成实际值（如 /chat/{conversation_id}）。

    未提供值的占位符替换为空串；没有占位符时原样返回。
    """
    result = str(path)
    for key, value in kwargs.items():
        result = result.replace(chr(123) + key + chr(125), '' if value is None else str(value))
    return result


_ROLE_LABELS = {"system": "系统", "user": "用户", "assistant": "助手", "tool": "工具结果"}


def _message_text(content: Any) -> str:
    """消息内容 -> 纯文本（多模态时只取文本分段，与 API 层一致）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content)


def render_messages_text(messages: list[dict[str, Any]] | None, *, max_chars: int = 0) -> str:
    """把 OpenAI ``messages`` 渲染成一段纯文本（**本地适配策略**）。

    抓包确认：上游 body 里没有 role/tools 字段，只有 ``prompt`` 一个字符串，
    因此 system（人格设定）与历史轮次只能以文本形式送进去：

        系统：<人格>

        用户：<历史提问>

        助手：<历史回答>

        用户：<当前提问>

    * ``max_chars > 0`` 时按"保留全部 system + 最近的轮次"截断，避免 prompt 过长；
    * 最后一条消息（通常是当前提问）一定会被保留。
    """
    if not messages:
        return ""
    rendered: list[tuple[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        label = _ROLE_LABELS.get(role, role)
        if role == "tool":
            name = message.get("name") or message.get("tool_call_id") or ""
            label = f"工具结果({name})" if name else "工具结果"
        rendered.append((role, f"{label}：{_message_text(message.get('content'))}"))

    head = [line for role, line in rendered if role == "system"]
    tail = [(role, line) for role, line in rendered if role != "system"]

    if max_chars and max_chars > 0 and tail:
        used = sum(len(line) + 2 for line in head)
        kept: list[str] = []
        for _role, line in reversed(tail):
            if kept and used + len(line) + 2 > max_chars:
                break
            kept.append(line)
            used += len(line) + 2
        tail_lines = list(reversed(kept))
    else:
        tail_lines = [line for _role, line in tail]
    return "\n\n".join(head + tail_lines)


def build_send_payload(
    profile: Profile,
    *,
    conversation_id: str | None,
    model: str,
    prompt: str,
    stream: bool,
    agent_id: str = "",
    messages: list[dict[str, Any]] | None = None,
    extra: Mapping[str, Any] | None = None,
    effort: str = "no_think",
    history_max_chars: int = 0,
) -> dict[str, Any]:
    """按 profile 模板构造发送消息的请求体。"""
    schema = profile.require_schema(
        "request_schema", what="发送消息的请求体结构（body_template）"
    )
    template = schema.get("body_template")
    if not isinstance(template, Mapping) or not template:
        raise ProtocolUnknownError(
            "request_schema.body_template", "body_template 为空或不是 mapping"
        )

    variables: dict[str, Any] = {
        "conversation_id": conversation_id,
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "agent_id": agent_id,
        "messages": messages or [],
        # 把 system/历史渲染成文本（上游只有 prompt 一个字符串字段）；无 messages 时回退为裸 prompt
        "messages_text": render_messages_text(messages, max_chars=history_max_chars) or prompt,
        "effort": effort,
    }
    payload = render_template(template, variables)
    if not isinstance(payload, dict):
        raise ProtocolUnknownError(
            "request_schema.body_template", "渲染后的请求体不是 JSON object"
        )
    if extra:
        payload.update({str(k): v for k, v in extra.items()})
    return payload


def history_mode(profile: Profile) -> str:
    """``prompt_only``（依赖上游会话上下文）或 ``full_history``（每次带全量历史）。"""
    schema = profile.require_schema("request_schema", what="会话历史传递方式")
    mode = str(schema.get("history_mode") or "").strip()
    if mode not in ("prompt_only", "full_history"):
        raise ProtocolUnknownError(
            "request_schema.history_mode",
            f"未确认取值：{mode!r}（应为 prompt_only 或 full_history）",
        )
    return mode


def build_stop_payload(profile: Profile, *, conversation_id: str | None, message_id: str | None = None) -> dict[str, Any]:
    """构造停止生成请求体；结构同样来自 profile。"""
    schema = profile.require_schema("request_schema", what="停止生成的请求体结构")
    template = schema.get("stop_body_template")
    if not isinstance(template, Mapping) or not template:
        raise ProtocolUnknownError(
            "request_schema.stop_body_template",
            "停止生成请求体结构未确认（需要抓包“停止生成”一次并记录 body）",
        )
    return render_template(template, {"conversation_id": conversation_id, "message_id": message_id})
