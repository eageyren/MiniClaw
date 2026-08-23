from __future__ import annotations

import re
from pathlib import Path

from ..core.paths import WorkspacePathError, resolve_workspace_path
from ..core.state import RuntimeState


def grep(state: RuntimeState, pattern: str, path: str = ".", glob: str = "**/*", head_limit: int = 100, ignore_case: bool = False) -> str:
    if head_limit <= 0:
        return "ERROR: head_limit must be > 0"
    try:
        expression = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        root = resolve_workspace_path(state.workspace, path, allow_root=True)
        if not root.exists():
            return f"ERROR: path does not exist: {path}"
        candidates = [root] if root.is_file() else sorted(root.glob(glob))
        results: list[str] = []
        for candidate in candidates:
            if len(results) >= head_limit:
                break
            if not candidate.is_file():
                continue
            try:
                safe = resolve_workspace_path(state.workspace, candidate)
                text = safe.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeError, WorkspacePathError):
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if expression.search(line):
                    relative = safe.relative_to(state.workspace).as_posix()
                    results.append(f"{relative}:{line_number}:{line}")
                    if len(results) >= head_limit:
                        break
        return "\n".join(results) if results else "No matches"
    except (re.error, WorkspacePathError, OSError) as exc:
        return f"ERROR: {exc}"


class GrepTool:
    name = "Grep"
    description = "Search workspace files with a regular expression."

    def __init__(self, state: RuntimeState):
        self.state = state

    def invoke(self, pattern: str, path: str = ".", glob: str = "**/*", head_limit: int = 100, ignore_case: bool = False) -> str:
        return grep(self.state, pattern, path, glob, head_limit, ignore_case)
