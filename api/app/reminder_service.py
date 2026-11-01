"""Deterministic recurrence and state transitions for durable reminders."""
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from sqlalchemy import select, update

from app.database import SessionLocal
from app.models import Reminder, ReminderDelivery, ReminderTrigger

WEEKDAY = {"一": "MO", "二": "TU", "三": "WE", "四": "TH", "五": "FR", "六": "SA", "日": "SU", "天": "SU"}
TRANSITIONS = {
    "draft": {"pending_confirmation"},
    "pending_confirmation": {"scheduled", "cancelled"},
    "scheduled": {"triggered", "completed", "paused", "cancelled", "needs_reconfirmation"},
    "triggered": {"completed", "cancelled"},
    "paused": {"scheduled", "completed", "cancelled", "needs_reconfirmation"},
    "needs_reconfirmation": {"pending_confirmation", "cancelled"},
    "completed": set(), "cancelled": set(),
}

def canonical_recurrence(text: str, due_at: datetime) -> str | None:
    value = re.sub(r"\s+", "", text)
    if any(word in value for word in ("不重复", "不用重复", "不再重复", "就这一次", "一次性")):
        return None
    if "每天" in value or "每日" in value or "每一天" in value:
        return "FREQ=DAILY"
    if "每周" in value or "每星期" in value:
        days = re.findall(r"(?:周|星期)([一二三四五六日天])", value)
        if days:
            return "FREQ=WEEKLY;BYDAY=" + ",".join(dict.fromkeys(WEEKDAY[day] for day in days))
        return "FREQ=WEEKLY"
    if "每月" in value:
        day_match = re.search(r"每月(?:的)?([0-9一二两三四五六七八九十]{1,3})号", value)
        if day_match:
            day = _chinese_number(day_match.group(1))
            if 1 <= day <= 31:
                return f"FREQ=MONTHLY;BYMONTHDAY={day}"
        return f"FREQ=MONTHLY;BYMONTHDAY={due_at.day}"
    if "每年" in value:
        return f"FREQ=YEARLY;BYMONTH={due_at.month};BYMONTHDAY={due_at.day}"
    raise ValueError("重复规则不明确，请说明每天、每周、每月、每年或不重复。")

def _chinese_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "零": 0}
    if value == "十": return 10
    if "十" in value:
        left, _, right = value.partition("十")
        return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)
    number = 0
    for char in value: number = number * 10 + digits[char]
    return number

def next_occurrence(rule: str | None, due_at: datetime, after: datetime, timezone_name: str) -> datetime | None:
    """Return next local-wall-time recurrence after `after`; None means one-shot complete."""
    if rule is None:
        return None
    zone = ZoneInfo(timezone_name)
    local_start = due_at.astimezone(zone)
    candidate = rrulestr(rule, dtstart=local_start).after(after.astimezone(zone), inc=False)
    return candidate

def transition(reminder: Reminder, target: str, expected_version: int) -> None:
    if reminder.version != expected_version:
        raise RuntimeError("提醒已被更新，请重新读取后再试。")
    if target not in TRANSITIONS.get(reminder.status, set()):
        raise ValueError(f"不能将提醒从{reminder.status}变更为{target}。")
    reminder.status = target
    reminder.version += 1

def apply_confirmed_change(db, payload: dict, family_id: str, member_id: str) -> Reminder:
    reminder = db.scalar(select(Reminder).where(Reminder.id == payload["reminder_id"], Reminder.family_id == family_id, Reminder.member_id == member_id).with_for_update())
    if not reminder:
        raise ValueError("这条提醒已经找不到了，请重新查看提醒列表。")
    expected_version = int(payload["expected_version"])
    operation = payload["operation"]
    if operation == "reschedule":
        if reminder.version != expected_version:
            raise RuntimeError("这条提醒刚刚有变化，请重新查看后再改。")
        due_at = datetime.fromisoformat(payload["due_at"])
        if due_at.tzinfo is None or due_at <= datetime.now(timezone.utc):
            raise ValueError("新的提醒时间无效或已经过去，请重新确认具体日期和时间。")
        try:
            rule = canonical_recurrence(reminder.recurrence or "不重复", due_at)
        except ValueError as exc:
            raise ValueError("原来的重复规则无法继续使用，请重新创建一条提醒。") from exc
        if reminder.status not in {"scheduled", "paused"}:
            raise ValueError("这条提醒当前状态不能改期，请重新查看提醒列表。")
        reminder.due_at = due_at
        reminder.next_trigger_at = due_at.astimezone(timezone.utc) if reminder.status == "scheduled" else None
        reminder.recurrence_rule = rule
        reminder.version += 1
        return reminder
    target = {"complete": "completed", "pause": "paused", "resume": "scheduled", "cancel": "cancelled"}.get(operation)
    if not target:
        raise ValueError("目前不支持这个提醒操作。")
    transition(reminder, target, expected_version)
    if target in {"paused", "completed", "cancelled"}:
        reminder.next_trigger_at = None
    elif target == "scheduled" and reminder.next_trigger_at is None:
        reminder.next_trigger_at = max(reminder.due_at, datetime.now(timezone.utc))
    if target in {"completed", "cancelled"}:
        delivery_status = "acknowledged" if target == "completed" else "ignored"
        trigger_ids = select(ReminderTrigger.id).where(ReminderTrigger.reminder_id == reminder.id)
        changed_at = datetime.now(timezone.utc)
        db.execute(update(ReminderDelivery).where(ReminderDelivery.trigger_id.in_(trigger_ids), ReminderDelivery.status.in_(["pending", "unacknowledged", "escalated"])).values(status=delivery_status, acknowledged_at=changed_at, status_updated_at=changed_at))
    return reminder

def activate_confirmed_reminder(confirmation, payload: dict) -> Reminder:
    due_at = datetime.fromisoformat(payload["due_at"])
    rule = canonical_recurrence(payload["recurrence"], due_at)
    with SessionLocal() as db:
        existing = db.scalar(select(Reminder).where(Reminder.idempotency_key == confirmation.id))
        if existing:
            return existing
        row = Reminder(id=str(uuid.uuid4()), family_id=confirmation.family_id, member_id=confirmation.member_id,
            title=payload["title"], due_at=due_at, timezone_name=payload["timezone"], recurrence=payload["recurrence"],
            recurrence_rule=rule, next_trigger_at=due_at, status="scheduled", version=1,
            idempotency_key=confirmation.id, source_conversation_id=confirmation.conversation_id,
            source_message_id=payload.get("source_message_id"), origin_request_id=payload.get("origin_request_id"))
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

def claim_due_batch(now: datetime, limit: int = 50) -> int:
    """Atomically claim and record due occurrences. Safe across scheduler processes."""
    processed = 0
    with SessionLocal() as db:
        reminders = db.scalars(select(Reminder).where(Reminder.status == "scheduled", Reminder.next_trigger_at <= now)
            .order_by(Reminder.next_trigger_at).limit(limit).with_for_update(skip_locked=True)).all()
        for reminder in reminders:
            scheduled_for = reminder.next_trigger_at
            if scheduled_for is None: continue
            existing = db.scalar(select(ReminderTrigger).where(ReminderTrigger.reminder_id == reminder.id, ReminderTrigger.scheduled_for == scheduled_for))
            if existing:
                reminder.status = "needs_reconfirmation"; reminder.version += 1
                continue
            trigger = ReminderTrigger(id=str(uuid.uuid4()), reminder_id=reminder.id, family_id=reminder.family_id,
                scheduled_for=scheduled_for, is_catch_up=(now - scheduled_for).total_seconds() > 60)
            db.add(trigger)
            db.flush()
            db.add(ReminderDelivery(id=str(uuid.uuid4()), trigger_id=trigger.id, channel="in_app", status="pending", idempotency_key=f"in_app:{trigger.id}", status_updated_at=now))
            processed += 1
            try:
                following = next_occurrence(reminder.recurrence_rule, reminder.due_at, max(now, scheduled_for), reminder.timezone_name)
            except Exception:
                reminder.status = "needs_reconfirmation"
                reminder.next_trigger_at = None
                reminder.version += 1
            else:
                if following is None:
                    reminder.status = "triggered"
                    reminder.next_trigger_at = None
                else:
                    reminder.next_trigger_at = following.astimezone(timezone.utc)
                reminder.version += 1
        db.commit()
    return processed

def acknowledge_delivery(delivery_id: str, family_id: str, member_id: str) -> bool:
    with SessionLocal() as db:
        row = db.scalar(select(ReminderDelivery).join(ReminderTrigger, ReminderTrigger.id == ReminderDelivery.trigger_id).join(Reminder, Reminder.id == ReminderTrigger.reminder_id).where(ReminderDelivery.id == delivery_id, Reminder.family_id == family_id, Reminder.member_id == member_id).with_for_update())
        if not row: return False
        if row.status != "acknowledged":
            row.status = "acknowledged"
            row.acknowledged_at = datetime.now(timezone.utc)
            row.status_updated_at = row.acknowledged_at
            trigger = db.get(ReminderTrigger, row.trigger_id)
            reminder = db.get(Reminder, trigger.reminder_id) if trigger else None
            if reminder and reminder.status == "triggered":
                reminder.status = "completed"
                reminder.version += 1
            db.commit()
        return True

def ignore_delivery(delivery_id: str, family_id: str, member_id: str) -> bool:
    with SessionLocal() as db:
        row = db.scalar(select(ReminderDelivery).join(ReminderTrigger, ReminderTrigger.id == ReminderDelivery.trigger_id).join(Reminder, Reminder.id == ReminderTrigger.reminder_id).where(ReminderDelivery.id == delivery_id, ReminderDelivery.channel == "in_app", Reminder.family_id == family_id, Reminder.member_id == member_id).with_for_update())
        if not row: return False
        if row.status not in {"acknowledged", "ignored"}:
            row.status = "ignored"
            row.acknowledged_at = datetime.now(timezone.utc)
            row.status_updated_at = row.acknowledged_at
            db.commit()
        return True
