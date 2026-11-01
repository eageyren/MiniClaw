from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models import CharacterRole, Family, Member, RoleToolPermission
from app.role_config import DEFAULT_ROLES
import uuid

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

def ensure_local_identity(db):
    if not db.get(Family, settings.family_id):
        db.add(Family(id=settings.family_id, name="本地开发家庭", timezone_name=settings.family_timezone))
    if not db.scalar(select(Member).where(Member.id == settings.member_id, Member.family_id == settings.family_id)):
        db.add(Member(id=settings.member_id, family_id=settings.family_id, kind="elder", display_name="长辈"))
    db.commit()
    for role in DEFAULT_ROLES:
        if not db.scalar(select(CharacterRole).where(CharacterRole.family_id == settings.family_id, CharacterRole.role_key == role["role_key"])):
            db.add(CharacterRole(id=str(uuid.uuid4()), family_id=settings.family_id, role_key=role["role_key"], config=role, version=1))
    db.commit()
    from app.tools import TOOL_REGISTRY
    from app.access_service import default_tool_grant
    roles = db.scalars(select(CharacterRole).where(CharacterRole.family_id == settings.family_id)).all()
    for role in roles:
        for tool in TOOL_REGISTRY.values():
            existing = db.scalar(select(RoleToolPermission).where(RoleToolPermission.family_id == settings.family_id, RoleToolPermission.role_id == role.id, RoleToolPermission.tool_name == tool.name))
            if not existing:
                enabled = default_tool_grant(role.role_key, tool.name)
                db.add(RoleToolPermission(id=str(uuid.uuid4()), family_id=settings.family_id, role_id=role.id, tool_name=tool.name, enabled=enabled))
        cancel_permission = db.scalar(select(RoleToolPermission).where(RoleToolPermission.family_id == settings.family_id,
            RoleToolPermission.role_id == role.id, RoleToolPermission.tool_name == "cancel_reminder"))
        if not cancel_permission:
            db.add(RoleToolPermission(id=str(uuid.uuid4()), family_id=settings.family_id, role_id=role.id,
                tool_name="cancel_reminder", enabled=default_tool_grant(role.role_key, "cancel_reminder")))
    db.commit()
