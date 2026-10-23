"""本地工具桥接单测（app/adapters/tool_bridge.py）。

覆盖：说明文本渲染、tool_choice 三态、JSON 解析（多种形状）、幻觉调用防护、失败回退。
"""

from __future__ import annotations

import json

from app.adapters import tool_bridge

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询天气",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


def test_render_tools_instructions_contains_spec_and_example():
    text = tool_bridge.render_tools_instructions(TOOLS)
    assert text is not None
    assert tool_bridge.INSTRUCTION_HEADER in text
    assert "get_weather" in text and "查询天气" in text
    assert '"city"' in text  # JSON Schema 被带上
    assert tool_bridge.OUTPUT_EXAMPLE in text


def test_render_tools_instructions_handles_flat_shape_and_empty():
    flat = [{"name": "search", "description": "搜索"}]
    text = tool_bridge.render_tools_instructions(flat)
    assert text is not None and "search" in text
    assert tool_bridge.render_tools_instructions([]) is None
    assert tool_bridge.render_tools_instructions(None) is None


def test_tool_choice_semantics():
    assert tool_bridge.tool_choice_disables_tools("none") is True
    assert tool_bridge.tool_choice_disables_tools("auto") is False
    assert tool_bridge.forced_tool_name({"type": "function", "function": {"name": "get_weather"}}) == (
        "get_weather"
    )
    assert tool_bridge.forced_tool_name("required") == ""
    forced_text = tool_bridge.render_tools_instructions(TOOLS, "required")
    assert forced_text is not None and "必须" in forced_text
    named = tool_bridge.render_tools_instructions(TOOLS, {"name": "get_weather"})
    assert named is not None and "必须*调用工具 get_weather" in named.replace("**", "*")


def test_build_openai_tool_call_shape():
    call = tool_bridge.build_openai_tool_call("get_weather", {"city": "北京"})
    assert call["type"] == "function"
    assert call["id"].startswith("call_")
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "北京"}


def test_parse_tool_calls_plain_json():
    calls, remaining = tool_bridge.parse_tool_calls(
        '{"tool_calls":[{"name":"get_weather","arguments":{"city":"北京"}}]}'
    )
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "get_weather"
    assert json.loads(calls[0]["function"]["arguments"]) == {"city": "北京"}
    assert remaining == ""


def test_parse_tool_calls_fenced_and_nested_shapes():
    fenced = '```json\n{"tool_calls":[{"function":{"name":"get_weather","arguments":"{\\"city\\":\\"北京\\"}"}}]}\n```'
    calls, _ = tool_bridge.parse_tool_calls(fenced)
    assert len(calls) == 1
    assert json.loads(calls[0]["function"]["arguments"]) == {"city": "北京"}

    single = '{"name":"get_weather","arguments":{"city":"上海"}}'
    calls, _ = tool_bridge.parse_tool_calls(single)
    assert len(calls) == 1 and json.loads(calls[0]["function"]["arguments"]) == {"city": "上海"}

    multi = '[{"name":"a","arguments":{}},{"name":"b","arguments":{}}]'
    calls, _ = tool_bridge.parse_tool_calls(multi)
    assert [call["function"]["name"] for call in calls] == ["a", "b"]


def test_parse_tool_calls_keeps_surrounding_text():
    calls, remaining = tool_bridge.parse_tool_calls(
        '好的，我来查一下。\n{"tool_calls":[{"name":"get_weather","arguments":{}}]}'
    )
    assert len(calls) == 1
    assert remaining == "好的，我来查一下。"


def test_parse_tool_calls_rejects_unknown_tool_names():
    """防幻觉：模型复述提示词里的示例（name="<工具名>"）时必须回退为普通文本。"""
    example = tool_bridge.OUTPUT_EXAMPLE
    calls, remaining = tool_bridge.parse_tool_calls(example, allowed_names=["get_weather"])
    assert calls == []
    assert remaining == example

    ok, _ = tool_bridge.parse_tool_calls(
        '{"tool_calls":[{"name":"get_weather","arguments":{}}]}', allowed_names=["get_weather"]
    )
    assert len(ok) == 1


def test_parse_tool_calls_returns_original_when_not_json():
    for text in ("", "今天天气不错", "{不是合法 json", '{"foo":1}'):
        calls, remaining = tool_bridge.parse_tool_calls(text)
        assert calls == []
        assert remaining == text