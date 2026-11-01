from app.access_service import default_tool_grant
from app.role_config import DEFAULT_ROLES, build_system_prompt, validate_role_config


def test_daughter_and_granddaughter_have_distinct_write_grants():
    assert default_tool_grant("daughter", "propose_reminder") is True
    assert default_tool_grant("daughter", "cancel_reminder") is True
    assert default_tool_grant("granddaughter", "propose_reminder") is False
    assert default_tool_grant("granddaughter", "cancel_reminder") is False
    assert default_tool_grant("granddaughter", "get_current_time") is True


def test_unknown_tool_is_not_granted():
    assert default_tool_grant("daughter", "run_shell_command") is False


def test_prompt_has_identity_safety_and_separated_memory_and_tools():
    config = validate_role_config(DEFAULT_ROLES[0])
    prompt = build_system_prompt(config, shared_facts=["长辈喜欢散步"], available_tools=["查询时间"])
    assert prompt.index("你是 MiniClaw") < prompt.index("角色：") < prompt.index("经授权的家庭共享事实")
    assert prompt.index("可用工具") < prompt.index("安全边界")
    assert "必须明确说不是" in prompt
    assert "长辈喜欢散步" in prompt


def test_role_configuration_does_not_accept_prompt_supplied_tool_grants():
    config = dict(DEFAULT_ROLES[0])
    config["tool_allowlist"] = ["propose_reminder"]
    try:
        validate_role_config(config)
    except ValueError as error:
        assert "不支持配置工具" in str(error)
    else:
        raise AssertionError("角色文案不能授予工具权限")
