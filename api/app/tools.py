import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import FamilyFact, Reminder

logger = logging.getLogger(__name__)
MAX_RESULT_CHARS = 4000

@dataclass(frozen=True)
class ToolContext:
    family_id: str
    member_id: str
    default_timezone: str = "Asia/Shanghai"
    allowed_tools: frozenset[str] | None = None
    request_id: str | None = None

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    read_only: bool
    requires_confirmation: bool
    timeout_seconds: float
    handler: Callable[[dict[str, Any], ToolContext], Awaitable[Any]]

def _query_time(timezone_name: str) -> datetime:
    return datetime.now(ZoneInfo(timezone_name))

async def _current_time(args: dict, context: ToolContext):
    zone_name = args.get("timezone", context.default_timezone)
    try:
        now = _query_time(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("请使用有效的时区名称，例如 Asia/Shanghai") from exc
    weekday = "星期" + "一二三四五六日"[now.weekday()]
    return {"timezone": zone_name, "datetime": now.isoformat(timespec="seconds"), "weekday": weekday}

def _read_reminders(context: ToolContext, limit: int, status_filter: str = "scheduled"):
    with SessionLocal() as db:
        statement = select(Reminder).where(Reminder.family_id == context.family_id, Reminder.member_id == context.member_id)
        if status_filter == "scheduled":
            statement = statement.where(Reminder.status == "scheduled", Reminder.next_trigger_at >= datetime.now(timezone.utc)).order_by(Reminder.next_trigger_at)
        elif status_filter == "paused":
            statement = statement.where(Reminder.status == "paused").order_by(Reminder.due_at)
        else:
            statement = statement.where(Reminder.status.in_(["scheduled", "paused", "triggered", "needs_reconfirmation"])).order_by(Reminder.due_at)
        rows = db.scalars(statement.limit(limit)).all()
        return [{"id": row.id, "version": row.version, "title": row.title, "due_at": (row.next_trigger_at or row.due_at).isoformat(), "timezone": row.timezone_name, "recurrence": row.recurrence, "status": row.status} for row in rows]

async def _list_reminders(args: dict, context: ToolContext):
    limit = args.get("limit", 10)
    return {"items": await asyncio.to_thread(_read_reminders, context, limit, args.get("status", "scheduled"))}

def _read_shared_facts(context: ToolContext, query: str | None, limit: int):
    statement = select(FamilyFact).where(FamilyFact.family_id == context.family_id, FamilyFact.scope == "shared", FamilyFact.authorized.is_(True), FamilyFact.status == "active")
    if query:
        statement = statement.where(FamilyFact.content.ilike(f"%{query}%"))
    with SessionLocal() as db:
        rows = db.scalars(statement.order_by(FamilyFact.created_at.desc()).limit(limit)).all()
        return [{"type": row.fact_type, "content": row.content} for row in rows]

async def _read_family_facts(args: dict, context: ToolContext):
    return {"items": await asyncio.to_thread(_read_shared_facts, context, args.get("query"), args.get("limit", 10))}

TOOL_REGISTRY: dict[str, ToolSpec] = {
    "get_current_time": ToolSpec(
        name="get_current_time",
        description="查询此刻的日期、时间、时区和星期。用户问现在几点、今天几号或星期几时使用；不用于推断其他人的日程。",
        parameters={"type": "object", "properties": {"timezone": {"type": "string", "description": "IANA 时区，例如 Asia/Shanghai"}}, "additionalProperties": False},
        read_only=True, requires_confirmation=False, timeout_seconds=2.0, handler=_current_time,
    ),
    "list_reminders": ToolSpec(
        name="list_reminders",
        description="查看提醒列表；可以查即将发生的、暂停的，或当前全部有效/待重新确认的提醒。修改提醒前必须先查列表获取 id 和 version。",
        parameters={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}, "status": {"type": "string", "enum": ["scheduled", "paused", "all"], "default": "scheduled"}}, "additionalProperties": False},
        read_only=True, requires_confirmation=False, timeout_seconds=3.0, handler=_list_reminders,
    ),
    "read_shared_family_facts": ToolSpec(
        name="read_shared_family_facts",
        description="查询长辈已授权给陪伴角色使用的家庭共享事实，例如称呼和纪念日。只在回答需要家庭事实时使用；不得读取未授权或私有内容。",
        parameters={"type": "object", "properties": {"query": {"type": "string", "maxLength": 80}, "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}}, "additionalProperties": False},
        read_only=True, requires_confirmation=False, timeout_seconds=3.0, handler=_read_family_facts,
    ),
    "propose_reminder": ToolSpec(
        name="propose_reminder",
        description="提出创建提醒的候选，必须经长辈在复述界面点击按钮确认。仅在用户明确要求创建提醒且给出事项、日期时间、时区和重复规则时使用；不能猜测。",
        parameters={
            "type": "object", "required": ["title", "due_at", "timezone", "recurrence"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 300},
                "due_at": {"type": "string", "format": "date-time", "description": "长辈明确说出的 ISO 8601 日期时间，必须带时区偏移"},
                "timezone": {"type": "string", "description": "IANA 时区，例如 Asia/Shanghai"},
                "recurrence": {"type": "string", "minLength": 1, "maxLength": 200, "description": "明确重复规则；一次性填写 不重复"},
            }, "additionalProperties": False,
        },
        read_only=False, requires_confirmation=True, timeout_seconds=35.0, handler=_list_reminders,
    ),
    "propose_reminder_change": ToolSpec(
        name="propose_reminder_change",
        description="提出完成、改期、暂停、继续或取消已有提醒的候选。先用 list_reminders 读取 id 和 version；如果有多条可能匹配的提醒，先向长辈询问，不要猜。任何操作都必须经长辈点击确认；取消和改期需要二次确认。改期必须使用长辈明确说出的新日期和时间。",
        parameters={
            "type": "object", "required": ["reminder_id", "expected_version", "operation"],
            "properties": {
                "reminder_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "expected_version": {"type": "integer", "minimum": 1},
                "operation": {"type": "string", "enum": ["complete", "reschedule", "pause", "resume", "cancel"]},
                "due_at": {"type": "string", "format": "date-time"},
                "timezone": {"type": "string"},
            }, "additionalProperties": False,
        },
        read_only=False, requires_confirmation=True, timeout_seconds=35.0, handler=_list_reminders,
    ),
}

def openai_tool_schemas(allowed_tools: frozenset[str] | set[str] | None = None) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters}} for spec in TOOL_REGISTRY.values() if (spec.read_only or spec.requires_confirmation) and (allowed_tools is None or spec.name in allowed_tools)]

async def execute_tool(name: str, raw_args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    queried_at = datetime.now(ZoneInfo(context.default_timezone)).isoformat(timespec="seconds")
    started = time.perf_counter()
    spec = TOOL_REGISTRY.get(name)
    if context.allowed_tools is not None and name not in context.allowed_tools:
        return {"ok": False, "error": "当前陪伴角色没有使用这项工具的权限。", "queried_at": queried_at, "duration_ms": 0}
    if not spec or not spec.read_only:
        return {"ok": False, "error": "这个查询目前不可用。", "queried_at": queried_at, "duration_ms": 0}
    try:
        if not isinstance(raw_args, dict):
            raise ValueError("参数必须是对象")
        errors = list(Draft202012Validator(spec.parameters).iter_errors(raw_args))
        if errors:
            raise ValueError("查询条件不完整或格式不正确，请换一种问法。")
        result = await asyncio.wait_for(spec.handler(raw_args, context), timeout=spec.timeout_seconds)
        payload: dict[str, Any] = {"ok": True, "result": result}
    except asyncio.TimeoutError:
        payload = {"ok": False, "error": "我查得有点慢，要不咱们待会儿再看。"}
    except ValueError as exc:
        payload = {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.warning("Read tool %s failed: %s", name, type(exc).__name__)
        payload = {"ok": False, "error": "这项信息暂时没查到，咱们待会儿再看。"}
    payload["queried_at"] = queried_at
    payload["duration_ms"] = int((time.perf_counter() - started) * 1000)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > MAX_RESULT_CHARS:
        raw_result = json.dumps(payload.get("result"), ensure_ascii=False, separators=(",", ":"))
        payload["result"] = raw_result[:MAX_RESULT_CHARS - 100] + "…[结果过长，已截断]"
        payload["truncated"] = True
    return payload
