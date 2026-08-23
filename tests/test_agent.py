from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from miniclaw.core import agent


class FakeTool:
    name = "Echo"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke(self, args: dict) -> str:
        self.calls.append(args)
        return "你好"


class FakeBoundModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = iter(responses)
        self.seen_messages = []

    def invoke(self, messages):
        self.seen_messages.append(list(messages))
        return next(self.responses)


class FakeModel:
    def __init__(self, bound: FakeBoundModel) -> None:
        self.bound = bound
        self.tools = None

    def bind_tools(self, tools):
        self.tools = tools
        return self.bound


def test_stream_agent_events(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool = FakeTool()
    bound = FakeBoundModel(
        [
            AIMessage(
                content="先执行工具",
                tool_calls=[{"name": "Echo", "args": {"value": 1}, "id": "call-1"}],
            ),
            AIMessage(content="完成"),
        ]
    )
    model = FakeModel(bound)
    monkeypatch.setattr(agent, "build_tools", lambda state: [tool])
    monkeypatch.setattr(agent, "create_model", lambda: model)

    events = list(agent.stream_agent_events("任务", workspace=tmp_path))

    assert [event["type"] for event in events] == [
        "ai_message", "tool_call", "tool_result", "ai_message", "final_answer"
    ]
    assert tool.calls == [{"value": 1}]
    first_messages = bound.seen_messages[0]
    assert first_messages[0].type == "system"
    assert first_messages[0].content == agent.ACTOR_PROMPT
    assert first_messages[1].type == "human"
    second_messages = bound.seen_messages[1]
    tool_message = second_messages[-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.name == "Echo"
    assert json.loads(tool_message.content) == "你好"
    assert events[-1] == {"type": "final_answer", "content": "完成"}


def test_max_loops_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_loops"):
        list(agent.stream_agent_events("任务", workspace=tmp_path, max_loops=0))
