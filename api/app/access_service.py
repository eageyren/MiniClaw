"""Tenant scoped role/tool authorization queries and updates."""
import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.models import CharacterRole, Family, RoleToolPermission
from app.tools import TOOL_REGISTRY

POLICY_ONLY_PERMISSIONS = {"cancel_reminder": "取消提醒（需要再次确认）"}

def default_tool_grant(role_key: str, tool_name: str) -> bool:
    if tool_name == "cancel_reminder":
        return role_key == "daughter"
    tool = TOOL_REGISTRY.get(tool_name)
    return bool(tool and (tool.read_only or (role_key == "daughter" and tool.requires_confirmation)))

def family_timezone(family_id: str) -> str:
    with SessionLocal() as db:
        family = db.get(Family, family_id)
        return family.timezone_name if family else "Asia/Shanghai"

def allowed_tools(family_id: str, role_id: str) -> frozenset[str]:
    with SessionLocal() as db:
        names = db.scalars(select(RoleToolPermission.tool_name).where(RoleToolPermission.family_id == family_id, RoleToolPermission.role_id == role_id, RoleToolPermission.enabled.is_(True))).all()
    return frozenset(name for name in names if name in TOOL_REGISTRY or name in POLICY_ONLY_PERMISSIONS)

def permission_matrix(family_id: str) -> list[dict]:
    with SessionLocal() as db:
        roles = db.scalars(select(CharacterRole).where(CharacterRole.family_id == family_id)).all()
        permissions = db.scalars(select(RoleToolPermission).where(RoleToolPermission.family_id == family_id)).all()
    enabled = {(p.role_id, p.tool_name): p.enabled for p in permissions}
    matrix = []
    for role in roles:
        tools = [{"name": tool.name, "description": tool.description, "enabled": enabled.get((role.id, tool.name), False)} for tool in TOOL_REGISTRY.values()]
        tools.extend({"name": name, "description": description, "enabled": enabled.get((role.id, name), False)} for name, description in POLICY_ONLY_PERMISSIONS.items())
        matrix.append({"role_key": role.role_key, "role_id": role.id, "role_name": role.config["name"], "tools": tools})
    return matrix

def set_tool_permission(family_id: str, role_key: str, tool_name: str, enabled: bool) -> dict:
    if tool_name not in TOOL_REGISTRY and tool_name not in POLICY_ONLY_PERMISSIONS: raise LookupError("找不到这项工具")
    with SessionLocal() as db:
        role = db.scalar(select(CharacterRole).where(CharacterRole.family_id == family_id, CharacterRole.role_key == role_key))
        if not role: raise LookupError("找不到这个陪伴角色")
        row = db.scalar(select(RoleToolPermission).where(RoleToolPermission.family_id == family_id, RoleToolPermission.role_id == role.id, RoleToolPermission.tool_name == tool_name).with_for_update())
        if not row:
            row = RoleToolPermission(id=str(uuid.uuid4()), family_id=family_id, role_id=role.id, tool_name=tool_name, enabled=enabled)
            db.add(row)
        else: row.enabled = enabled
        db.commit()
        return {"role_key": role_key, "tool_name": tool_name, "enabled": enabled}
