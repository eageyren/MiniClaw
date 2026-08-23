from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import ensure_workspace


@dataclass(frozen=True)
class RuntimeState:
    """Immutable runtime context shared by all workspace tools."""

    workspace: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", ensure_workspace(self.workspace))
