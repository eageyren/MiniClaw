"""Family settings repository and audit helpers (authentication is added in S10)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import CharacterRole, Conversation, FamilyChangeAudit, FamilyFact, Memory, Message, Reminder
from app.role_config import validate_role_config

def add_audit(db, family_id: str, actor: str, entity_type: str, entity_id: str, action: str, before_value, after_value, elder_summary: str):
    db.add(FamilyChangeAudit(id=str(uuid.uuid4()), family_id=family_id, actor=actor, entity_type=entity_type,
        entity_id=entity_id, action=action, before_value=before_value, after_value=after_value, elder_summary=elder_summary))

def update_role(family_id: str, actor: str, role_key: str, expected_version: int, raw_config: dict) -> int:
    config = validate_role_config(raw_config)
    with SessionLocal() as db:
        row = db.scalar(select(CharacterRole).where(CharacterRole.family_id == family_id, CharacterRole.role_key == role_key).with_for_update())
        if not row: raise LookupError("找不到这个陪伴角色")
        if row.version != expected_version: raise RuntimeError("角色配置刚刚有变化，请重新读取后再保存。")
        before = row.config
        row.config = config
        row.version += 1
        add_audit(db, family_id, actor, "role", row.id, "updated", before, config, f"{config['name']}角色的称呼和说话方式已调整。")
        db.commit()
        return row.version

def add_family_fact(family_id: str, actor: str, fact_type: str, content: str, authorized: bool) -> str:
    fact_id = str(uuid.uuid4())
    with SessionLocal() as db:
        row = FamilyFact(id=fact_id, family_id=family_id, fact_type=fact_type.strip(), content=content.strip(), scope="shared", authorized=authorized, status="active")
        db.add(row)
        add_audit(db, family_id, actor, "family_fact", fact_id, "created", None, {"fact_type": row.fact_type, "content": row.content, "authorized": authorized}, "家庭共享资料已添加。")
        db.commit()
    return fact_id

def delete_family_fact(family_id: str, actor: str, fact_id: str) -> bool:
    with SessionLocal() as db:
        row = db.scalar(select(FamilyFact).where(FamilyFact.id == fact_id, FamilyFact.family_id == family_id).with_for_update())
        if not row: return False
        before = {"fact_type": row.fact_type, "content": row.content, "authorized": row.authorized}
        add_audit(db, family_id, actor, "family_fact", row.id, "deleted", before, None, "一条家庭共享资料已删除。")
        db.delete(row)
        db.commit()
        return True

def update_family_fact(family_id: str, actor: str, fact_id: str, fact_type: str, content: str, authorized: bool) -> bool:
    with SessionLocal() as db:
        row = db.scalar(select(FamilyFact).where(FamilyFact.id == fact_id, FamilyFact.family_id == family_id).with_for_update())
        if not row: return False
        before = {"fact_type": row.fact_type, "content": row.content, "authorized": row.authorized}
        row.fact_type = fact_type.strip(); row.content = content.strip(); row.authorized = authorized
        after = {"fact_type": row.fact_type, "content": row.content, "authorized": row.authorized}
        add_audit(db, family_id, actor, "family_fact", row.id, "updated", before, after, "一条家庭共享资料已修改。")
        db.commit()
        return True

def shared_memories(family_id: str, member_id: str) -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(select(Memory, Message.content.label("source_text")).outerjoin(Message, Message.id == Memory.source_message_id).where(Memory.family_id == family_id, Memory.member_id == member_id,
            Memory.scope == "family_shared", Memory.status == "active", Memory.deleted_at.is_(None), Memory.authorized.is_(True)).order_by(Memory.created_at.desc())).all()
        return [{"id": row.id, "content": row.content, "created_at": row.created_at.isoformat(), "source_conversation_id": row.source_conversation_id, "source_text": (source or "").strip()} for row, source in rows]

def delete_shared_memory(family_id: str, member_id: str, actor: str, memory_id: str) -> bool:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.scalar(select(Memory).where(Memory.id == memory_id, Memory.family_id == family_id, Memory.member_id == member_id,
            Memory.scope == "family_shared", Memory.status == "active", Memory.authorized.is_(True)).with_for_update())
        if not row: return False
        before = {"scope": row.scope, "content": row.content}
        row.status = "deleted"; row.deleted_at = now
        add_audit(db, family_id, actor, "shared_memory", row.id, "deleted", before, None, "一条家庭共享记忆已删除。")
        db.commit()
        return True

def export_family_data(family_id: str, member_id: str) -> dict:
    with SessionLocal() as db:
        roles = db.scalars(select(CharacterRole).where(CharacterRole.family_id == family_id)).all()
        facts = db.scalars(select(FamilyFact).where(FamilyFact.family_id == family_id)).all()
        reminders = db.scalars(select(Reminder).where(Reminder.family_id == family_id, Reminder.member_id == member_id)).all()
        memories = db.scalars(select(Memory).where(Memory.family_id == family_id, Memory.member_id == member_id, Memory.scope == "family_shared", Memory.authorized.is_(True), Memory.status == "active", Memory.deleted_at.is_(None))).all()
        return {"exported_at": datetime.now(timezone.utc).isoformat(),
            "roles": [{"role_key": r.role_key, "config": r.config, "version": r.version} for r in roles],
            "family_facts": [{"type": f.fact_type, "content": f.content, "authorized": f.authorized} for f in facts],
            "reminders": [{"title": r.title, "due_at": r.due_at.isoformat(), "timezone": r.timezone_name, "recurrence": r.recurrence, "status": r.status} for r in reminders],
            "memories": [{"content": m.content, "scope": m.scope, "created_at": m.created_at.isoformat()} for m in memories]}

def delete_family_data(family_id: str, member_id: str, actor: str) -> int:
    with SessionLocal() as db:
        conversations = db.scalars(select(Conversation.id).where(Conversation.family_id == family_id, Conversation.member_id == member_id)).all()
        db.execute(delete(Conversation).where(Conversation.family_id == family_id, Conversation.member_id == member_id))
        reminders_result = db.execute(delete(Reminder).where(Reminder.family_id == family_id, Reminder.member_id == member_id))
        db.execute(delete(FamilyFact).where(FamilyFact.family_id == family_id))
        add_audit(db, family_id, actor, "family_data", family_id, "deleted", {"conversation_count": len(conversations), "reminder_count": reminders_result.rowcount or 0}, None, "聊天记录、提醒和家庭共享资料已清除。")
        db.commit()
        return len(conversations)
