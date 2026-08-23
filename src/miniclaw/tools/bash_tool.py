from __future__ import annotations

import subprocess

from ..core.state import RuntimeState


def bash(state: RuntimeState, command: str, timeout_seconds: float = 30.0) -> str:
    if timeout_seconds <= 0:
        return "ERROR: timeout_seconds must be > 0"
    try:
        completed = subprocess.run(
            command,
            cwd=state.workspace,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return f"ERROR: command timed out after {timeout_seconds:g}s: {exc.cmd}"
    except OSError as exc:
        return f"ERROR: could not start command: {exc}"
    output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    return f"exit_code={completed.returncode}\n{output}".rstrip()


class BashTool:
    name = "Bash"
    description = "Run a shell command with the workspace as cwd; this is not a strict sandbox."

    def __init__(self, state: RuntimeState):
        self.state = state

    def invoke(self, command: str, timeout_seconds: float = 30.0) -> str:
        return bash(self.state, command, timeout_seconds)
