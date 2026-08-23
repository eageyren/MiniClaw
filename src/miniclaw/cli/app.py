from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from ..core.agent import stream_agent_events

app = typer.Typer(add_completion=False, help="A workspace-scoped tool-calling CLI agent.")
console = Console()


@app.command()
def main(
    task: str = typer.Argument(..., help="Task for the agent."),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory."),
    max_loops: int = typer.Option(10, "--max-loops", min=1, help="Maximum model loops."),
) -> None:
    try:
        root = workspace or ".miniclaw-workspace"
        for event in stream_agent_events(task, workspace=root, max_loops=max_loops):
            event_type = event.get("type")
            if event_type == "tool_call":
                args = json.dumps(event.get("args", {}), ensure_ascii=False)
                console.print(f"[bold cyan]Tool:[/bold cyan] {event.get('name')} {args}")
            elif event_type == "tool_result":
                console.print(Panel(str(event.get("result", "")), title=f"Result: {event.get('name')}"))
            elif event_type == "final_answer":
                console.print(Panel(Markdown(str(event.get("content", ""))), title="Final answer"))
    except (RuntimeError, ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
