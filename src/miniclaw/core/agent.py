from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ..tools.registry import build_tools
from .state import RuntimeState

ACTOR_PROMPT = """You are the actor node in miniclaw's ReAct workflow.

You implement the user's task using tools. Work inside the workspace only.

Rules:
- Use FileWriteTool for new files.
- Use FileReadTool before editing existing files.
- Use FileEditTool for focused edits.
- Use BashTool to run commands and test results.
- BashTool already runs inside the workspace. Use relative paths, never "cd /workspace".
- End with a concise summary of files changed and commands run.
"""


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(
        str(item.get("text", item)) if isinstance(item, dict) else str(item)
        for item in content
    )


def create_model() -> Any:
    from ..providers import create_model as provider_create_model

    return provider_create_model()


def stream_agent_events(
    task: str,
    *,
    workspace: str | Path,
    max_loops: int = 10,
) -> Iterator[dict]:
    if max_loops <= 0:
        raise ValueError("max_loops must be > 0")

    state = RuntimeState(workspace)
    tools = build_tools(state)
    tools_by_name = {tool.name: tool for tool in tools}
    bound_model = create_model().bind_tools(tools)
    messages: list[Any] = [
        SystemMessage(content=ACTOR_PROMPT),
        HumanMessage(content=task),
    ]
    last_content = ""
    exhausted = False

    for _ in range(max_loops):
        response = bound_model.invoke(messages)
        if not isinstance(response, AIMessage):
            response = AIMessage(
                content=getattr(response, "content", str(response)),
                tool_calls=getattr(response, "tool_calls", []) or [],
            )
        messages.append(response)
        last_content = _message_text(response)
        calls = response.tool_calls or []
        yield {
            "type": "ai_message",
            "content": last_content,
            "tool_calls": calls,
        }

        if not calls:
            break

        for call in calls:
            name = call.get("name", "")
            args = call.get("args", {})
            call_id = call.get("id", "")
            yield {"type": "tool_call", "name": name, "args": args}

            tool = tools_by_name.get(name)
            if tool is None:
                result: Any = f"ERROR [UnknownTool]: {name}"
            else:
                try:
                    result = tool.invoke(args)
                except Exception as exc:
                    result = f"ERROR: {exc}"

            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=call_id,
                    name=name,
                )
            )
            yield {"type": "tool_result", "name": name, "result": result}
    else:
        exhausted = True

    if exhausted:
        notice = f"Stopped after reaching the maximum of {max_loops} model loops."
        last_content = f"{last_content}\n\n{notice}" if last_content else notice

    yield {"type": "final_answer", "content": last_content}
