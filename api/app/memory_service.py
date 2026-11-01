"""Small, explicit-consent memory store with role and family visibility boundaries."""
import re
import uuid
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, update

from app.context_manager import count_tokens
from app.database import SessionLocal
from app.models import CharacterRole, Memory, Message

OPT_IN = re.compile(r"你记着|帮我记住|请记住|记一下|记住我")
OPT_OUT = re.compile(r"别记|不要记|不用记|不许记|忘掉这条|把这条忘了")
SENSITIVE = re.compile(r"药|吃药|服药|剂量|生病|疾病|医院|疼|痛|不舒服|抑郁|焦虑|生气|吵架|抱怨|讨厌|恨|转账|银行卡|密码|身份证")
PREFERENCE = re.compile(r"(?:我)?(?:喜欢|爱听|爱看|爱好|习惯)([^，,。！？；\n]{1,160})")
ADDRESS = re.compile(r"(?:以后)?叫我([\u4e00-\u9fffA-Za-z0-9]{1,20})")
SHARED_SCOPE = re.compile(r"给全家记|大家都可以知道|其他角色也能用|全家都记住")
MAX_MEMORY_TOKENS = 800
logger = logging.getLogger(__name__)

def is_memory_opt_out(text: str) -> bool:
    return bool(OPT_OUT.search(text))

def capture_explicit_memory(family_id: str, member_id: str, conversation_id: str, message_id: str, role_id: str | None, text: str) -> str:
    if is_memory_opt_out(text):
        return "refused"
    if not OPT_IN.search(text):
        return "not_requested"
    if SENSITIVE.search(text):
        return "not_saved_sensitive"
    match = PREFERENCE.search(text)
    address_match = ADDRESS.search(text)
    if match:
        content = "明确偏好：" + match.group(0).strip()
    elif address_match:
        content = "称呼偏好：希望被称为“" + address_match.group(1) + "”"
    else:
        return "not_supported"
    if len(content) > 500:
        content = content[:500]
    scope = "family_shared" if SHARED_SCOPE.search(text) else "role_private"
    try:
        with SessionLocal() as db:
            db.add(Memory(id=str(uuid.uuid4()), family_id=family_id, member_id=member_id, scope=scope,
                role_id=None if scope == "family_shared" else role_id, content=content,
                source_conversation_id=conversation_id, source_message_id=message_id, confidence=0.98,
                authorized=scope == "family_shared", status="active"))
            db.commit()
    except Exception as exc:
        logger.warning("Explicit memory write failed: %s", type(exc).__name__)
        return "not_saved_error"
    return "saved_shared" if scope == "family_shared" else "saved_private"

def relevant_memories(family_id: str, member_id: str, role_id: str | None) -> list[dict]:
    now = datetime.now(timezone.utc)
    role_private = (Memory.scope == "role_private") & (Memory.role_id == role_id) if role_id else False
    visibility = or_(role_private, (Memory.scope == "family_shared") & Memory.authorized.is_(True))
    statement = select(Memory, Message.content.label("source_text")).outerjoin(Message, Message.id == Memory.source_message_id).where(
        Memory.family_id == family_id, Memory.member_id == member_id, Memory.status == "active",
        Memory.deleted_at.is_(None), or_(Memory.expires_at.is_(None), Memory.expires_at > now), visibility
    ).order_by(Memory.created_at.desc())
    try:
        with SessionLocal() as db:
            rows = db.execute(statement).all()
    except Exception:
        # Memory is optional context; a query failure must not interrupt ordinary conversation.
        return []
    result = []
    used_tokens = 0
    for memory, source_text in rows:
        item = {"content": memory.content, "scope": memory.scope, "source": (source_text or "").strip()[:180], "created_at": memory.created_at.isoformat()}
        size = count_tokens(str(item))
        if used_tokens + size > MAX_MEMORY_TOKENS:
            break
        result.append(item)
        used_tokens += size
    return result

def list_member_memories(family_id: str, member_id: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = db.execute(select(Memory, CharacterRole.role_key, Message.content.label("source_text"))
            .outerjoin(CharacterRole, CharacterRole.id == Memory.role_id)
            .outerjoin(Message, Message.id == Memory.source_message_id)
            .where(Memory.family_id == family_id, Memory.member_id == member_id, Memory.status == "active", Memory.deleted_at.is_(None), or_(Memory.expires_at.is_(None), Memory.expires_at > now))
            .order_by(Memory.created_at.desc())).all()
        return [{"id": memory.id, "content": memory.content, "scope": memory.scope, "authorized": memory.authorized, "role_key": role_key,
            "source_conversation_id": memory.source_conversation_id, "source_text": (source or "").strip(),
            "confidence": memory.confidence, "created_at": memory.created_at.isoformat(),
            "expires_at": memory.expires_at.isoformat() if memory.expires_at else None}
            for memory, role_key, source in rows]

def soft_delete_memory(memory_id: str, family_id: str, member_id: str) -> bool:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.scalar(select(Memory).where(Memory.id == memory_id, Memory.family_id == family_id, Memory.member_id == member_id, Memory.status == "active", Memory.deleted_at.is_(None)).with_for_update())
        if not row: return False
        row.status = "deleted"
        row.deleted_at = now
        db.commit()
        return True

def soft_delete_role_memories(role_id: str, family_id: str, member_id: str) -> int:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        result = db.execute(update(Memory).where(Memory.role_id == role_id, Memory.family_id == family_id, Memory.member_id == member_id, Memory.status == "active").values(status="deleted", deleted_at=now))
        db.commit()
        return result.rowcount or 0

def revoke_shared_memories(family_id: str, member_id: str) -> int:
    with SessionLocal() as db:
        result = db.execute(update(Memory).where(Memory.family_id == family_id, Memory.member_id == member_id, Memory.scope == "family_shared", Memory.authorized.is_(True)).values(authorized=False))
        db.commit()
        return result.rowcount or 0

def cleanup_expired_memories(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    retention_cutoff = now - timedelta(days=30)
    with SessionLocal() as db:
        result = db.execute(delete(Memory).where(or_(Memory.expires_at <= now, (Memory.status == "deleted") & (Memory.deleted_at <= retention_cutoff))))
        db.commit()
        return result.rowcount or 0
