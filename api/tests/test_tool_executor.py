import asyncio

from app.tools import ToolContext, ToolSpec, execute_tool
import app.tools as tools_module


def test_read_tool_validates_arguments_and_handles_unknown_timezone():
    context = ToolContext("family-test", "member-test", "Asia/Shanghai", frozenset({"get_current_time"}))
    good = asyncio.run(execute_tool("get_current_time", {"timezone": "Asia/Shanghai"}, context))
    assert good["ok"] is True
    assert good["result"]["timezone"] == "Asia/Shanghai"
    bad = asyncio.run(execute_tool("get_current_time", {"timezone": "not/a-zone"}, context))
    assert bad["ok"] is False
    assert "时区" in bad["error"]


def test_tool_access_is_enforced_before_handler_runs(monkeypatch):
    called = False

    async def handler(_args, _context):
        nonlocal called
        called = True
        return {"secret": "value"}

    spec = ToolSpec("policy_test", "policy test", {"type": "object"}, True, False, 1, handler)
    monkeypatch.setitem(tools_module.TOOL_REGISTRY, "policy_test", spec)
    result = asyncio.run(execute_tool("policy_test", {}, ToolContext("f", "m", allowed_tools=frozenset())))
    assert result["ok"] is False
    assert called is False


def test_read_tool_timeout_becomes_a_small_user_safe_result(monkeypatch):
    async def slow(_args, _context):
        await asyncio.sleep(0.05)
        return {"ok": True}

    spec = ToolSpec("slow_test", "slow test", {"type": "object"}, True, False, 0.001, slow)
    monkeypatch.setitem(tools_module.TOOL_REGISTRY, "slow_test", spec)
    result = asyncio.run(execute_tool("slow_test", {}, ToolContext("f", "m", allowed_tools=frozenset({"slow_test"}))))
    assert result["ok"] is False
    assert "待会儿再看" in result["error"]
