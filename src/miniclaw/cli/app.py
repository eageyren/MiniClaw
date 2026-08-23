from __future__ import annotations

from typing import Any

import typer
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ..core.state import RuntimeState
from ..tools.registry import build_tools

app = typer.Typer(add_completion=False, help="A workspace-scoped tool-calling CLI agent.")


def _text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)


def run_agent(model: Any, tools: list[Any], task: str, max_rounds: int = 20) -> str:
    if max_rounds <= 0:
        raise ValueError("max_rounds must be > 0")
    by_name = {tool.name: tool for tool in tools}
    bound_model = model.bind_tools(tools)
    messages: list[Any] = [HumanMessage(content=task)]
    last_text = ""
    for _ in range(max_rounds):
        response = bound_model.invoke(messages)
        if not isinstance(response, AIMessage):
            response = AIMessage(content=getattr(response, "content", str(response)))
        messages.append(response)
        last_text = _text(response)
        calls = getattr(response, "tool_calls", []) or []
        if not calls:
            return last_text
        for call in calls:
            name = call.get("name", "")
            call_id = call.get("id", "")
            tool = by_name.get(name)
            if tool is None:
                result = f"ERROR [UnknownTool]: {name}"
            else:
                try:
                    result = tool.invoke(call.get("args", {}))
                except Exception as exc:  # tool failures belong in the conversation
                    result = f"ERROR: {exc}"
            messages.append(ToolMessage(content=str(result), tool_call_id=call_id, name=name))
    return f"{last_text}\n\nStopped after reaching the maximum of {max_rounds} model rounds."


@app.command()
def main(
    task: str = typer.Argument(..., help="Task for the agent."),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory."),
    max_rounds: int = typer.Option(20, min=1, help="Maximum model rounds."),
) -> None:
    try:
        root = workspace or ".miniclaw-workspace"
        state = RuntimeState(root)
        tools = build_tools(state)
        from ..providers import create_model

        result = run_agent(create_model(), tools, task, max_rounds)
    except (RuntimeError, ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(result)
