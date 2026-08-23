from __future__ import annotations

from functools import partial

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..core.state import RuntimeState
from .bash_tool import bash
from .file_tools import file_edit, file_read, file_write
from .grep_tool import grep


class FileReadArgs(BaseModel):
    file_path: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, gt=0)


class FileWriteArgs(BaseModel):
    file_path: str
    content: str


class FileEditArgs(BaseModel):
    file_path: str
    old_text: str
    new_text: str


class GrepArgs(BaseModel):
    pattern: str
    path: str = "."
    glob: str = "**/*"
    head_limit: int = Field(default=100, gt=0)
    ignore_case: bool = False


class BashArgs(BaseModel):
    command: str
    timeout_seconds: float = Field(default=30.0, gt=0)


def build_tools(state: RuntimeState) -> list[StructuredTool]:
    return [
        StructuredTool.from_function(partial(file_read, state), name="FileRead", description="Read UTF-8 lines from a workspace file.", args_schema=FileReadArgs),
        StructuredTool.from_function(partial(file_write, state), name="FileWrite", description="Create or overwrite a UTF-8 workspace file.", args_schema=FileWriteArgs),
        StructuredTool.from_function(partial(file_edit, state), name="FileEdit", description="Replace exactly one text occurrence in a workspace file.", args_schema=FileEditArgs),
        StructuredTool.from_function(partial(grep, state), name="Grep", description="Search workspace files with a regular expression.", args_schema=GrepArgs),
        StructuredTool.from_function(partial(bash, state), name="Bash", description="Run a shell command with workspace as cwd; not a strict sandbox.", args_schema=BashArgs),
    ]
