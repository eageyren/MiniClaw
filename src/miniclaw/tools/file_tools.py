from __future__ import annotations

from pathlib import Path

from ..core.paths import WorkspacePathError, resolve_workspace_path
from ..core.state import RuntimeState


def _error(message: str) -> str:
    return f"ERROR: {message}"


def file_read(state: RuntimeState, file_path: str, offset: int = 0, limit: int = 2000) -> str:
    if offset < 0 or limit <= 0:
        return _error("offset must be >= 0 and limit must be > 0")
    try:
        path = resolve_workspace_path(state.workspace, file_path)
        if not path.is_file():
            return _error(f"Not a file: {file_path}")
        lines = path.read_text(encoding="utf-8").splitlines()
        selected = lines[offset : offset + limit]
        body = "\n".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(selected))
        return f"OK: {file_path} lines {offset + 1}-{offset + len(selected)}\n{body}" if body else f"OK: {file_path} (no lines)"
    except (OSError, UnicodeError, WorkspacePathError) as exc:
        return _error(str(exc))


def file_write(state: RuntimeState, file_path: str, content: str) -> str:
    try:
        path = resolve_workspace_path(state.workspace, file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"OK: wrote {len(content.encode('utf-8'))} bytes to {file_path}"
    except (OSError, UnicodeError, WorkspacePathError) as exc:
        return _error(str(exc))


def file_edit(state: RuntimeState, file_path: str, old_text: str, new_text: str) -> str:
    try:
        path = resolve_workspace_path(state.workspace, file_path)
        if not path.is_file():
            return _error(f"Not a file: {file_path}")
        original = path.read_text(encoding="utf-8")
        count = original.count(old_text)
        if count == 0:
            return _error(f"Text not found; file was not modified: {file_path}")
        if count != 1:
            return _error(f"Text matched {count} times; file was not modified: {file_path}")
        path.write_text(original.replace(old_text, new_text, 1), encoding="utf-8")
        return f"OK: edited {file_path}"
    except (OSError, UnicodeError, WorkspacePathError) as exc:
        return _error(str(exc))


class FileReadTool:
    name = "FileRead"
    description = "Read UTF-8 lines from a file inside the workspace."

    def __init__(self, state: RuntimeState):
        self.state = state

    def invoke(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        return file_read(self.state, file_path, offset, limit)


class FileWriteTool:
    name = "FileWrite"
    description = "Create or overwrite a UTF-8 file inside the workspace."

    def __init__(self, state: RuntimeState):
        self.state = state

    def invoke(self, file_path: str, content: str) -> str:
        return file_write(self.state, file_path, content)


class FileEditTool:
    name = "FileEdit"
    description = "Replace exactly one occurrence of text in a workspace file."

    def __init__(self, state: RuntimeState):
        self.state = state

    def invoke(self, file_path: str, old_text: str, new_text: str) -> str:
        return file_edit(self.state, file_path, old_text, new_text)
