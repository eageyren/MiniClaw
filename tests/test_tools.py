from pathlib import Path

import pytest

from miniclaw.core.paths import WorkspacePathError, resolve_workspace_path
from miniclaw.core.state import RuntimeState
from miniclaw.tools.file_tools import file_edit, file_read, file_write
from miniclaw.tools.grep_tool import grep


def test_paths_reject_escape(tmp_path: Path) -> None:
    state = RuntimeState(tmp_path / "workspace")
    with pytest.raises(WorkspacePathError):
        resolve_workspace_path(state.workspace, "../outside.txt")


def test_file_tools_and_unique_edit(tmp_path: Path) -> None:
    state = RuntimeState(tmp_path)
    assert file_write(state, "nested/a.txt", "one\ntwo\n").startswith("OK")
    assert "1\tone" in file_read(state, "nested/a.txt")
    assert file_edit(state, "nested/a.txt", "two", "THREE").startswith("OK")
    assert file_read(state, "nested/a.txt").find("THREE") >= 0
    file_write(state, "same.txt", "x x")
    assert "not modified" in file_edit(state, "same.txt", "x", "y")


def test_grep(tmp_path: Path) -> None:
    state = RuntimeState(tmp_path)
    file_write(state, "a.py", "Alpha\nbeta\n")
    assert "a.py:1:Alpha" in grep(state, "alpha", glob="**/*.py", ignore_case=True)
