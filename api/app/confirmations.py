import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph.types import interrupt
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Confirmation, ConfirmationAudit, Message, Reminder
from app.reminder_service import activate_confirmed_reminder, apply_confirmed_change
from app.reminder_service import canonical_recurrence
from app.tools import ToolContext

CONFIRMATION_SECONDS = 30
RISK_PATTERN = re.compile(r"药|吃药|服药|剂量|转账|汇款|打钱|付款|金额|[0-9一二两三四五六七八九十]+元|块钱|给(?:小?[王李张刘陈赵孙周吴郑冯蒋沈韩杨朱秦何吕施孔曹严华金魏陶姜|儿子|女儿|医生|邻居|老伴).{0,8}(?:转|钱|送|寄|买|汇|打电话)|(?:小王|小李|儿子|女儿|医生|邻居)")

def create_reminder_candidate(args: dict, context: ToolContext, conversation_id: str, tool_call_id: str, user_text: str) -> dict:
    required = ("title", "due_at", "timezone", "recurrence")
    missing = [field for field in required if not isinstance(args.get(field), str) or not args[field].strip()]
    if missing:
        return {"ok": False, "needs_clarification": True, "missing": missing, "message": "请先补问事项、具体时间、时区和重复规则，不要猜。"}
    missing_from_speech = []
    has_day = re.search(r"今天|明天|后天|大后天|今早|今晚|明早|明晚|\d{1,2}月\d{1,2}[日号]|\d{1,2}号|星期[一二三四五六日天]|周[一二三四五六日天]|\d+小时后", user_text)
    has_clock = re.search(r"早上|上午|中午|下午|晚上|凌晨|[零一二两三四五六七八九十\d]{1,3}点|\d{1,2}[:：]\d{2}", user_text)
    if not has_day: missing_from_speech.append("具体日期")
    if not has_clock: missing_from_speech.append("具体时间")
    if not re.search(r"每(?:天|日|周|星期|月|年)|每天|每周|每月|每年|一次|不重复|不用重复|不再重复|就这一次", user_text):
        missing_from_speech.append("重复规则")
    if missing_from_speech:
        return {"ok": False, "needs_clarification": True, "missing": missing_from_speech, "message": "长辈没有明确说出这些信息，请先追问，不能替长辈决定。"}
    try:
        zone = ZoneInfo(args["timezone"])
        due_at = datetime.fromisoformat(args["due_at"].replace("Z", "+00:00"))
    except (ValueError, ZoneInfoNotFoundError):
        return {"ok": False, "needs_clarification": True, "message": "时间或时区不清楚，请先向长辈确认。"}
    if due_at.tzinfo is None:
        return {"ok": False, "needs_clarification": True, "message": "时间没有时区，请先问清楚当地时间。"}
    due_at = due_at.astimezone(zone)
    if due_at <= datetime.now(timezone.utc):
        return {"ok": False, "needs_clarification": True, "message": "这个时间已经过去了，请再问长辈一个未来的时间。"}
    try:
        canonical_recurrence(args["recurrence"], due_at)
    except ValueError as exc:
        return {"ok": False, "needs_clarification": True, "message": str(exc)}
    title = args["title"].strip()
    recurrence = args["recurrence"].strip()
    high_risk = bool(RISK_PATTERN.search(title))
    local_due = due_at.strftime("%Y年%m月%d日 %H:%M")
    recite = f"提醒您：{title}；时间是{local_due}（{args['timezone']}）；重复规则是{recurrence}。"
    if high_risk:
        recite += "这个比较重要，我再跟您确认一次。"
    now = datetime.now(timezone.utc)
    confirmation_id = str(uuid.uuid4())
    payload = {"title": title, "due_at": due_at.isoformat(), "timezone": args["timezone"], "recurrence": recurrence, "tool_call_id": tool_call_id}
    if context.request_id: payload["origin_request_id"] = context.request_id
    with SessionLocal() as db:
        source = db.scalar(select(Message).where(Message.conversation_id == conversation_id, Message.role == "user").order_by(Message.created_at.desc()).limit(1))
        if source: payload["source_message_id"] = source.id
        db.add(Confirmation(id=confirmation_id, family_id=context.family_id, member_id=context.member_id, conversation_id=conversation_id, action="create_reminder", payload=payload, recite_text=recite, high_risk=high_risk, status="pending", step=0, created_at=now, expires_at=now + timedelta(seconds=CONFIRMATION_SECONDS), step_started_at=now))
        db.commit()
    return {"confirmation_id": confirmation_id, "tool_call_id": tool_call_id, "high_risk": high_risk, "payload": payload, "recite_text": recite}

def create_reminder_change_candidate(args: dict, context: ToolContext, conversation_id: str, tool_call_id: str, user_text: str) -> dict:
    reminder_id = args.get("reminder_id")
    operation = args.get("operation")
    try: expected_version = int(args.get("expected_version"))
    except (TypeError, ValueError): return {"ok": False, "needs_clarification": True, "message": "我没找到提醒的最新版本，请先重新查看提醒列表。"}
    if operation not in {"complete", "reschedule", "pause", "resume", "cancel"}:
        return {"ok": False, "needs_clarification": True, "message": "请确认是完成、改期、暂停、继续还是取消这条提醒。"}
    if operation == "cancel" and "cancel_reminder" not in (context.allowed_tools or frozenset()):
        return {"ok": False, "needs_clarification": True, "message": "当前陪伴角色不能替您取消提醒，请让家属检查权限设置。"}
    with SessionLocal() as db:
        reminder = db.scalar(select(Reminder).where(Reminder.id == reminder_id, Reminder.family_id == context.family_id, Reminder.member_id == context.member_id))
        if not reminder: return {"ok": False, "needs_clarification": True, "message": "我没找到这条提醒，请先重新查看提醒列表。"}
        if reminder.version != expected_version:
            return {"ok": False, "needs_clarification": True, "message": "这条提醒刚刚有变化，请先重新查看提醒列表。"}
        allowed_states = {"complete": {"scheduled", "triggered", "paused"}, "reschedule": {"scheduled", "paused"}, "pause": {"scheduled"}, "resume": {"paused"}, "cancel": {"scheduled", "triggered", "paused", "needs_reconfirmation"}}
        if reminder.status not in allowed_states[operation]:
            return {"ok": False, "needs_clarification": True, "message": "这条提醒当前已经处理过或状态不合适，请先重新查看提醒列表。"}
        now_due = reminder.next_trigger_at or reminder.due_at
        local_current = now_due.astimezone(ZoneInfo(reminder.timezone_name)).strftime("%Y年%m月%d日 %H:%M")
        payload = {"reminder_id": reminder.id, "expected_version": reminder.version, "operation": operation, "timezone": reminder.timezone_name}
        labels = {"complete": "完成", "pause": "暂停", "resume": "继续", "cancel": "取消", "reschedule": "改期"}
        if operation == "reschedule":
            if not re.search(r"今天|明天|后天|大后天|\d{1,2}月\d{1,2}[日号]|\d{1,2}号|星期[一二三四五六日天]|周[一二三四五六日天]", user_text) or not re.search(r"早上|上午|中午|下午|晚上|凌晨|[零一二两三四五六七八九十\d]{1,3}点|\d{1,2}[:：]\d{2}", user_text):
                return {"ok": False, "needs_clarification": True, "message": "改期需要长辈明确说出新的日期和具体时间，请先追问。"}
            try:
                zone = ZoneInfo(reminder.timezone_name)
                new_due = datetime.fromisoformat(args["due_at"].replace("Z", "+00:00"))
                if new_due.tzinfo is None or new_due <= datetime.now(timezone.utc): raise ValueError()
                new_due = new_due.astimezone(zone)
            except (KeyError, ValueError, ZoneInfoNotFoundError):
                return {"ok": False, "needs_clarification": True, "message": "新的日期、时间或时区不清楚，请先问清楚。"}
            payload.update({"due_at": new_due.isoformat(), "timezone": zone.key})
            recite = f"您要把“{reminder.title}”从{local_current}改到{new_due.strftime('%Y年%m月%d日 %H:%M')}（{zone.key}），重复规则仍是{reminder.recurrence or '不重复'}，对吗？"
        elif operation == "cancel":
            recite = f"您要取消“{reminder.title}”，原定时间是{local_current}。取消以后就不会提醒了，您确定吗？"
        else:
            recite = f"您要{labels[operation]}“{reminder.title}”这条提醒，原定时间是{local_current}，重复规则是{reminder.recurrence or '不重复'}，对吗？"
        high_risk = operation in {"cancel", "reschedule"} or bool(RISK_PATTERN.search(reminder.title))
        source = db.scalar(select(Message).where(Message.conversation_id == conversation_id, Message.role == "user").order_by(Message.created_at.desc()).limit(1))
        if source: payload["source_message_id"] = source.id
        confirmation_id = str(uuid.uuid4()); now = datetime.now(timezone.utc)
        db.add(Confirmation(id=confirmation_id, family_id=context.family_id, member_id=context.member_id, conversation_id=conversation_id, action=f"reminder_{operation}", payload=payload, recite_text=recite, high_risk=high_risk, status="pending", step=0, created_at=now, expires_at=now + timedelta(seconds=CONFIRMATION_SECONDS), step_started_at=now))
        db.commit()
    return {"confirmation_id": confirmation_id, "tool_call_id": tool_call_id, "high_risk": high_risk, "payload": payload, "recite_text": recite}

def _confirmation_snapshot(confirmation_id: str):
    with SessionLocal() as db:
        row = db.get(Confirmation, confirmation_id)
        if not row: return None
        return {"id": row.id, "family_id": row.family_id, "member_id": row.member_id, "conversation_id": row.conversation_id, "action": row.action, "payload": row.payload, "recite_text": row.recite_text, "high_risk": row.high_risk, "status": row.status, "step": row.step, "expires_at": row.expires_at, "step_started_at": row.step_started_at}

def _apply_decision(snapshot: dict, answer: dict, step: int) -> tuple[str, bool]:
    """Persist one button decision idempotently. Returns (result, high-risk-step-required)."""
    accepted = bool(answer.get("accepted")) and answer.get("confirmation_id") == snapshot["id"]
    trigger = answer.get("trigger", "button")
    if trigger not in {"button", "timeout"}:
        accepted = False
        trigger = "button"
    now = datetime.now(timezone.utc)
    result = "confirmed" if accepted else ("expired" if trigger == "timeout" else "rejected")
    needs_second = accepted and snapshot["high_risk"] and step == 0
    with SessionLocal() as db:
        row = db.scalar(select(Confirmation).where(Confirmation.id == snapshot["id"]).with_for_update())
        if not row: return "expired", False
        # After the first high-risk approval, the node restarts from interrupt 1 on resume.
        if step == 0 and row.step >= 1:
            return "first_confirmed", True
        if step == 1 and row.step < 1:
            return "rejected", False
        audit_result = "first_confirmed" if needs_second else result
        existing = db.scalar(select(ConfirmationAudit).where(ConfirmationAudit.confirmation_id == row.id, ConfirmationAudit.step == step))
        if not existing:
            elapsed = max(0, int((now - row.step_started_at).total_seconds() * 1000))
            db.add(ConfirmationAudit(id=str(uuid.uuid4()), confirmation_id=row.id, family_id=row.family_id, actor=row.member_id, action=row.action, recite_text=row.recite_text, result=audit_result, trigger=trigger, step=step, duration_ms=elapsed))
        if needs_second:
            row.step = 1
            row.status = "pending"
            row.step_started_at = now
            row.expires_at = now + timedelta(seconds=CONFIRMATION_SECONDS)
            row.resume_claimed_at = None
            db.commit()
            return "first_confirmed", True
        if accepted:
            try:
                if row.action == "create_reminder":
                    activate_confirmed_reminder(row, row.payload)
                else:
                    apply_confirmed_change(db, row.payload, row.family_id, row.member_id)
            except (ValueError, RuntimeError):
                row.status = "needs_reconfirmation"
                row.resolved_at = now
                db.commit()
                return "rejected", False
            row.status = "confirmed"
        else:
            row.status = "expired" if trigger == "timeout" else "rejected"
        row.resolved_at = now
        row.resume_claimed_at = None
        db.commit()
    return result, False

async def confirmation_node(state, writer) -> dict:
    pending = state.get("pending_confirmation") or {}
    confirmation_id = pending.get("confirmation_id")
    if not confirmation_id:
        return _finish_confirmation(state, "那咱们这次先不记，需要的时候再说。", {"ok": False, "error": "确认记录已失效。"})
    step_number = 0
    snapshot = _confirmation_snapshot(confirmation_id)
    if not snapshot:
        return _finish_confirmation(state, "那咱们这次先不记，需要的时候再说。", {"ok": False, "error": "确认记录已失效。"})
    event = {"confirmation_id": confirmation_id, "step": 1, "total_steps": 2 if snapshot["high_risk"] else 1, "recite_text": snapshot["recite_text"], "expires_at": snapshot["expires_at"].isoformat(), "high_risk": snapshot["high_risk"]}
    writer({"confirmation": event})
    first = interrupt(event)
    first_result, needs_second = _apply_decision(snapshot, first if isinstance(first, dict) else {}, 0)
    if needs_second:
        step_number = 1
        snapshot = _confirmation_snapshot(confirmation_id) or snapshot
        second_text = "这个比较重要，我再跟您确认一次。" + snapshot["recite_text"].replace("这个比较重要，我再跟您确认一次。", "")
        event = {"confirmation_id": confirmation_id, "step": 2, "total_steps": 2, "recite_text": second_text, "expires_at": snapshot["expires_at"].isoformat(), "high_risk": True}
        writer({"confirmation": event})
        second = interrupt(event)
        final_result, _ = _apply_decision(snapshot, second if isinstance(second, dict) else {}, step_number)
    elif first_result == "first_confirmed":
        second_text = "这个比较重要，我再跟您确认一次。" + snapshot["recite_text"].replace("这个比较重要，我再跟您确认一次。", "")
        event = {"confirmation_id": confirmation_id, "step": 2, "total_steps": 2, "recite_text": second_text, "expires_at": snapshot["expires_at"].isoformat(), "high_risk": True}
        writer({"confirmation": event})
        second = interrupt(event)
        final_result, _ = _apply_decision(snapshot, second if isinstance(second, dict) else {}, 1)
    else:
        final_result = first_result
    if final_result == "confirmed":
        if snapshot["action"] == "create_reminder":
            answer_text = "好的，已经帮您记下这条提醒。"
            tool_result = {"ok": True, "created": True, "confirmation_id": confirmation_id, "message": answer_text}
        else:
            operation = snapshot["payload"].get("operation")
            answer_text = {"complete": "好的，这条提醒已经完成。", "pause": "好的，这条提醒已经暂停。", "resume": "好的，这条提醒已经继续安排。", "cancel": "好的，这条提醒已经取消，以后不会再提醒您。", "reschedule": "好的，这条提醒已经改好时间。"}.get(operation, "好的，提醒已经更新。")
            tool_result = {"ok": True, "updated": True, "operation": operation, "confirmation_id": confirmation_id, "message": answer_text}
    elif final_result == "expired":
        answer_text = "那咱们这次先不记，需要的时候再说。"
        tool_result = {"ok": False, "expired": True, "message": answer_text}
    else:
        answer_text = "好的，这次没有记录。您可以重新说一遍。"
        tool_result = {"ok": False, "rejected": True, "message": answer_text}
    writer({"token": answer_text})
    return _finish_confirmation(state, answer_text, tool_result)

def _finish_confirmation(state, answer_text: str, tool_result: dict) -> dict:
    from langchain_core.messages import AIMessage, RemoveMessage
    removals = [RemoveMessage(id=message.id) for message in state.get("messages", [])]
    return {"messages": [*removals, AIMessage(content=answer_text)], "pending_confirmation": None}
