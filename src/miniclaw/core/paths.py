from __future__ import annotations

from pathlib import Path


class WorkspacePathError(ValueError):
    """Raised when a path resolves outside the configured workspace."""


def ensure_workspace(path: str | Path) -> Path:
    workspace = Path(path).expanduser().resolve()
    if workspace.exists() and not workspace.is_dir():
        raise WorkspacePathError(f"Workspace is not a directory: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def resolve_workspace_path(
    workspace: str | Path,
    user_path: str | Path,
    *,
    allow_root: bool = False,
) -> Path:
    root = Path(workspace).expanduser().resolve()
    candidate = Path(user_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError(
            f"Path is outside workspace: {user_path!s} (workspace: {root})"
        ) from exc
    if not allow_root and relative == Path("."):
        raise WorkspacePathError("The workspace root cannot be used as a file path")
    return resolved
