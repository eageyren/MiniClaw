import asyncio
import io
import json
import base64
import logging
import time
import uuid
import wave
import yaml
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from langgraph.types import Command
from app.config import settings
from app.database import SessionLocal, ensure_local_identity
from app.models import AccessSession, CharacterRole, Confirmation, Conversation, ConversationSummary, DeviceCredential, FamilyAccess, FamilyAccount, FamilyChangeAudit, FamilyFact, Message, PairingCode, PushSubscription, Reminder, ReminderDelivery, ReminderTrigger, RequestTrace, RoleSwitch, RoleToolPermission, TraceSpan, UsageEvent, VoiceMetric
from app.schemas import ChatRequest, ConfirmationDecision, DestructiveConfirmation, FamilyFactCreate, FamilyLoginRequest, FamilyPairRequest, OtpRequest, PairingStatusRequest, DeviceRefreshRequest, RoleConfigUpdate, RoleImport, RoleSelection, ToolPermissionUpdate, PushSubscriptionCreate, UsageEventCreate
from app.agent import checkpointed_graph
from app.voice import provider as speech_provider, read_recording
from app.role_config import build_system_prompt, validate_role_config
from app.context_manager import build_context, count_tokens
from app.tools import TOOL_REGISTRY
from app.reminder_service import acknowledge_delivery, ignore_delivery
from app.memory_service import capture_explicit_memory, cleanup_expired_memories, list_member_memories, relevant_memories, revoke_shared_memories, soft_delete_memory, soft_delete_role_memories
from app.family_service import add_audit, add_family_fact, delete_family_data, delete_family_fact, delete_shared_memory, export_family_data, shared_memories, update_family_fact, update_role
from app.auth import ACCESS_SECONDS, REFRESH_DAYS, Identity, authenticate_request, create_pairing, issue_access, issue_refresh_secret, digest, pair_family, poll_pairing, refresh_access, request_identity, require_role, reset_identity, send_otp, set_identity, login_family
from app.access_service import allowed_tools as role_allowed_tools, family_timezone, permission_matrix, set_tool_permission
from app.reminder_delivery_service import disable_subscription, family_unacknowledged, process_reminder_deliveries, subscription_settings, upsert_subscription
from app.observability import current_request_id, reset_request_id, set_request_id, trace_event, trace_finish, trace_start
from zoneinfo import ZoneInfo

AVAILABLE_TOOL_DESCRIPTIONS = [f"{spec.name}：{spec.description}" for spec in TOOL_REGISTRY.values() if spec.read_only or spec.requires_confirmation]

def descriptions_for_tools(names):
    return [f"{spec.name}：{spec.description}" for spec in TOOL_REGISTRY.values() if spec.name in names]

logger = logging.getLogger(__name__)

async def checkpoint_retention_worker(graph):
    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, settings.checkpoint_ttl_days))
            with SessionLocal() as db:
                stale_ids = db.scalars(select(Conversation.id).where(Conversation.updated_at < cutoff)).all()
            for conversation_id in stale_ids:
                await graph.checkpointer.adelete_thread(conversation_id)
            if stale_ids:
                logger.info("Deleted %d expired LangGraph checkpoint threads", len(stale_ids))
        except Exception as exc:
            logger.warning("Checkpoint retention cleanup failed: %s", type(exc).__name__)
        await asyncio.sleep(24 * 60 * 60)

async def confirmation_timeout_worker(graph):
    while True:
        now = datetime.now(timezone.utc)
        claimed = []
        try:
            with SessionLocal() as db:
                stale_claim = now - timedelta(seconds=15)
                rows = db.scalars(select(Confirmation).where(((Confirmation.status == "pending") & (Confirmation.expires_at <= now)) | ((Confirmation.status == "resuming") & (Confirmation.resume_claimed_at <= stale_claim))).with_for_update(skip_locked=True)).all()
                for row in rows:
                    if row.expires_at <= now:
                        row.status = "resuming"
                        row.resume_claimed_at = now
                        claimed.append((row.id, row.conversation_id, row.family_id, row.member_id))
                    else:
                        row.status = "pending"
                        row.resume_claimed_at = None
                db.commit()
            for confirmation_id, conversation_id, family_id, member_id in claimed:
                config = {"configurable": {"thread_id": conversation_id}}
                identity_token = set_identity(Identity(family_id, member_id, "elder", member_id, "长辈", "background"))
                try:
                    async for _ in graph.astream(Command(resume={"confirmation_id": confirmation_id, "accepted": False, "trigger": "timeout"}), config, stream_mode="custom"):
                        pass
                finally:
                    reset_identity(identity_token)
        except Exception as exc:
            logger.warning("Confirmation timeout recovery failed: %s", type(exc).__name__)
        await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(application: FastAPI):
    with SessionLocal() as db:
        ensure_local_identity(db)
    async with checkpointed_graph(settings.database_url, settings.checkpoint_schema) as graph:
        application.state.graph = graph
        retention_task = asyncio.create_task(checkpoint_retention_worker(graph))
        confirmation_task = asyncio.create_task(confirmation_timeout_worker(graph))
        memory_task = asyncio.create_task(memory_cleanup_worker())
        try:
            yield
        finally:
            for task in (retention_task, confirmation_task, memory_task):
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass

app = FastAPI(title="MiniClaw API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "Authorization"], expose_headers=["X-Request-ID"])

@app.middleware("http")
async def bearer_identity_middleware(request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/health" or request.url.path.startswith("/api/auth/"):
        return await call_next(request)
    identity = await authenticate_request(request)
    if identity is None:
        return JSONResponse({"detail": "需要重新配对或登录。"}, status_code=401)
    token = set_identity(identity)
    trace_id = str(uuid.uuid4())
    trace_token = set_request_id(trace_id)
    started = time.perf_counter()
    status_code = 500
    try:
        trace_start(trace_id, request.url.path, identity)
        response = await call_next(request)
        response.headers["X-Request-ID"] = trace_id
        status_code = response.status_code
        return response
    finally:
        trace_finish(trace_id, status_code, int((time.perf_counter() - started) * 1000))
        reset_request_id(trace_token)
        reset_identity(token)

def pending_confirmation(conversation_id: str):
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.scalar(select(Confirmation).where(Confirmation.conversation_id == conversation_id, Confirmation.family_id == request_identity().family_id, Confirmation.member_id == request_identity().member_id, Confirmation.status.in_(["pending", "resuming"]), Confirmation.expires_at > now).order_by(Confirmation.created_at.desc()))
        if not row: return None
        step_text = row.recite_text
        if row.high_risk and row.step == 1:
            step_text = "这个比较重要，我再跟您确认一次。" + row.recite_text.replace("这个比较重要，我再跟您确认一次。", "")
        return {"confirmation_id": row.id, "step": row.step + 1, "total_steps": 2 if row.high_risk else 1, "recite_text": step_text, "expires_at": row.expires_at.isoformat(), "high_risk": row.high_risk}

async def confirmation_response(event: dict, persist_message: bool = True):
    audio_encoded = None
    started = time.perf_counter()
    try:
        audio = await speech_provider.synthesize(event["recite_text"])
        audio_encoded = base64.b64encode(audio).decode("ascii")
        trace_event("confirmation_tts", int((time.perf_counter() - started) * 1000), "ok", {"characters": len(event["recite_text"])})
    except Exception:
        trace_event("confirmation_tts", int((time.perf_counter() - started) * 1000), "failed", {"provider": settings.tts_provider})
    if persist_message:
        with SessionLocal() as db:
            db.add(Message(id=str(uuid.uuid4()), family_id=request_identity().family_id, conversation_id=(db.get(Confirmation, event["confirmation_id"]).conversation_id), role="assistant", content=event["recite_text"]))
            db.commit()
    return {"status": "confirmation_required", **event, "audio_base64": audio_encoded, "audio_content_type": "audio/mpeg"}

async def prepare_context(conversation_id: str, prompt: str, current_text: str, exclude_message_id: str | None = None):
    with SessionLocal() as db:
        history_rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.family_id == request_identity().family_id).order_by(Message.created_at, Message.id)).all()
        if exclude_message_id:
            history_rows = [row for row in history_rows if row.id != exclude_message_id]
        summary_row = db.scalar(select(ConversationSummary).where(ConversationSummary.conversation_id == conversation_id, ConversationSummary.family_id == request_identity().family_id))
        old_summary = summary_row.summary if summary_row else ""
        old_covered_id = summary_row.covered_through_message_id if summary_row else None
        history = [{"id": row.id, "role": row.role, "content": row.content} for row in history_rows]
    result = await build_context(prompt, history, old_summary, old_covered_id, current_text)
    if result.covered_through_message_id and result.covered_through_message_id != old_covered_id and result.summary:
        with SessionLocal() as db:
            summary_row = db.scalar(select(ConversationSummary).where(ConversationSummary.conversation_id == conversation_id, ConversationSummary.family_id == request_identity().family_id))
            if summary_row:
                summary_row.summary = result.summary
                summary_row.covered_through_message_id = result.covered_through_message_id
                summary_row.token_count = result.summary_tokens
            else:
                db.add(ConversationSummary(id=str(uuid.uuid4()), family_id=request_identity().family_id, conversation_id=conversation_id, summary=result.summary, covered_through_message_id=result.covered_through_message_id, token_count=result.summary_tokens))
            db.commit()
    return result

def memory_write_instruction(result: str) -> str | None:
    if result.startswith("saved_"):
        return "用户明确同意保存的内容已记录，可以如实告诉用户；共享记忆仅在其明确要求全家共享时写入。"
    if result in {"not_supported", "not_saved_sensitive", "not_saved_error"}:
        return "用户请求记忆，但本轮没有保存。请明确说明没有记下，不得声称已记住。"
    return None

def record_internal_usage(event_type: str, conversation_id: str | None = None):
    request_id = current_request_id() or str(uuid.uuid4())
    with SessionLocal() as db:
        session_key = None
        if conversation_id:
            session_key = db.scalar(select(UsageEvent.session_key).where(
                UsageEvent.family_id == request_identity().family_id, UsageEvent.member_id == request_identity().member_id,
                UsageEvent.conversation_id == conversation_id, UsageEvent.event_type == "page_open",
                UsageEvent.created_at >= datetime.now(timezone.utc) - timedelta(hours=12))
                .order_by(UsageEvent.created_at.desc()).limit(1))
        db.add(UsageEvent(id=str(uuid.uuid4()), family_id=request_identity().family_id, member_id=request_identity().member_id,
            conversation_id=conversation_id, session_key=session_key or request_id, event_type=event_type, metadata_json={}))
        db.commit()

async def memory_cleanup_worker():
    while True:
        try:
            deleted = cleanup_expired_memories()
            if deleted: logger.info("Physically removed %d expired or retired memories", deleted)
        except Exception as exc:
            logger.warning("Memory cleanup failed: %s", type(exc).__name__)
        await asyncio.sleep(24 * 60 * 60)

@app.get("/health")
def health():
    try:
        with SessionLocal() as db:
            db.execute(select(1))
    except Exception as exc:
        logger.warning("Health check database unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail={"status": "recovering"}) from exc
    return {"status": "ok"}

@app.post("/api/auth/pairing-code")
def new_pairing_code():
    return create_pairing()

@app.post("/api/auth/dev-session")
def development_session():
    if settings.app_env != "development":
        raise HTTPException(status_code=404, detail="找不到这个接口")
    with SessionLocal() as db:
        device_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        refresh = issue_refresh_secret()
        db.add(DeviceCredential(id=device_id, family_id=settings.family_id, member_id=settings.member_id,
            pairing_code_id=None, refresh_hash=digest(refresh), device_label="开发验收临时会话",
            expires_at=now + timedelta(days=REFRESH_DAYS)))
        db.flush()
        access, expires = issue_access(db, settings.family_id, settings.member_id, "elder", settings.member_id, "长辈", device_id=device_id)
        db.commit()
    return {"access_token": access, "refresh_token": refresh, "expires_at": expires.isoformat(), "role": "elder"}

@app.post("/api/auth/otp")
async def request_family_otp(body: OtpRequest):
    try:
        development_code = await send_otp(body.phone_number)
    except ValueError as exc:
        raise HTTPException(429, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "验证码暂时发不出去，请稍后再试。") from exc
    response = {"status": "sent", "expires_in_seconds": 300}
    if development_code: response["development_code"] = development_code
    return response

@app.post("/api/auth/family/pair")
def pair_family_account(body: FamilyPairRequest):
    try: access, refresh, expires = pair_family(body.phone_number, body.display_name, body.pairing_code, body.otp)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    return {"access_token": access, "refresh_token": refresh, "expires_at": expires.isoformat(), "role": "family"}

@app.post("/api/auth/family/login")
def family_login(body: FamilyLoginRequest):
    try: access, refresh, expires = login_family(body.phone_number, body.otp)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    return {"access_token": access, "refresh_token": refresh, "expires_at": expires.isoformat(), "role": "family"}

@app.post("/api/auth/pairing/status")
def pairing_status(body: PairingStatusRequest):
    try: return poll_pairing(body.enrollment_secret)
    except LookupError as exc: raise HTTPException(410, str(exc)) from exc

@app.post("/api/auth/refresh")
def refresh_session(body: DeviceRefreshRequest):
    try: return refresh_access(body.refresh_token)
    except LookupError as exc: raise HTTPException(401, str(exc)) from exc

@app.get("/api/elder/access")
def elder_access_list():
    require_role("elder")
    with SessionLocal() as db:
        links = db.scalars(select(FamilyAccess).where(FamilyAccess.family_id == request_identity().family_id, FamilyAccess.member_id == request_identity().member_id, FamilyAccess.revoked_at.is_(None))).all()
        items = []
        for link in links:
            account = db.get(FamilyAccount, link.family_account_id)
            if account:
                phone = account.phone_number
                masked = phone[:3] + "****" + phone[-2:] if len(phone) >= 7 else "已绑定手机号"
                items.append({"id": link.id, "display_name": account.display_name, "phone": masked, "created_at": link.created_at.isoformat()})
        devices = db.scalars(select(DeviceCredential).where(DeviceCredential.family_id == request_identity().family_id, DeviceCredential.member_id == request_identity().member_id, DeviceCredential.revoked_at.is_(None))).all()
        device_items = [{"id": d.id, "label": d.device_label, "created_at": d.created_at.isoformat(), "current": d.id == request_identity().device_id} for d in devices]
    return {"family_access": items, "devices": device_items}

@app.post("/api/elder/access/{access_id}/revoke")
def revoke_family_access(access_id: str):
    require_role("elder")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        link = db.scalar(select(FamilyAccess).where(FamilyAccess.id == access_id, FamilyAccess.family_id == request_identity().family_id, FamilyAccess.member_id == request_identity().member_id, FamilyAccess.revoked_at.is_(None)).with_for_update())
        if not link: raise HTTPException(404, "找不到这项授权")
        link.revoked_at = now
        sessions = db.scalars(select(AccessSession).where(AccessSession.family_account_id == link.family_account_id, AccessSession.revoked_at.is_(None))).all()
        for session in sessions: session.revoked_at = now
        account = db.get(FamilyAccount, link.family_account_id)
        add_audit(db, request_identity().family_id, request_identity().display_name, "family_access", link.id, "revoked", {"display_name": account.display_name if account else "家属"}, {"revoked": True}, "已撤销一位家属的访问授权。")
        db.commit()
    return {"status": "revoked"}

@app.post("/api/elder/devices/{device_id}/revoke")
def revoke_elder_device(device_id: str):
    require_role("elder")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        device = db.scalar(select(DeviceCredential).where(DeviceCredential.id == device_id, DeviceCredential.family_id == request_identity().family_id, DeviceCredential.member_id == request_identity().member_id, DeviceCredential.revoked_at.is_(None)).with_for_update())
        if not device: raise HTTPException(404, "找不到这台设备")
        device.revoked_at = now
        for session in db.scalars(select(AccessSession).where(AccessSession.device_id == device.id, AccessSession.revoked_at.is_(None))).all(): session.revoked_at = now
        add_audit(db, request_identity().family_id, request_identity().display_name, "device", device.id, "revoked", {"label": device.device_label}, {"revoked": True}, "已解绑一台长辈设备。")
        db.commit()
    return {"status": "revoked"}

@app.get("/api/access/tool-permissions")
def get_tool_permissions():
    require_role("family")
    return {"items": permission_matrix(request_identity().family_id)}

@app.put("/api/access/tool-permissions")
def update_tool_permissions(body: ToolPermissionUpdate):
    require_role("family")
    try:
        before = next((row for row in permission_matrix(request_identity().family_id) if row["role_key"] == body.role_key), None)
        changed = set_tool_permission(request_identity().family_id, body.role_key, body.tool_name, body.enabled)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    with SessionLocal() as db:
        add_audit(db, request_identity().family_id, request_identity().display_name, "tool_permission", body.role_key, "updated", before, changed, f"已调整 {body.role_key} 的工具权限。")
        db.commit()
    return changed

@app.get("/api/push/vapid-public-key")
def get_push_public_key():
    if request_identity().role not in {"elder", "family"}: raise HTTPException(404, "找不到这个页面")
    if not settings.push_vapid_public_key: raise HTTPException(503, "网页推送还没有配置。")
    return {"public_key": settings.push_vapid_public_key}

@app.post("/api/push/subscriptions")
def save_push_subscription(body: PushSubscriptionCreate):
    identity = request_identity()
    if identity.role not in {"elder", "family"}: raise HTTPException(404, "找不到这个页面")
    try: return upsert_subscription(identity, body.model_dump())
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc

@app.get("/api/push/subscriptions/me")
def get_my_push_subscription():
    identity = request_identity()
    if identity.role not in {"elder", "family"}: raise HTTPException(404, "找不到这个页面")
    return subscription_settings(identity)

@app.delete("/api/push/subscriptions")
def remove_push_subscription(body: dict):
    identity = request_identity()
    if identity.role not in {"elder", "family"}: raise HTTPException(404, "找不到这个页面")
    endpoint = body.get("endpoint") if isinstance(body, dict) else None
    if not isinstance(endpoint, str): raise HTTPException(422, "推送设备信息无效")
    if not disable_subscription(identity, endpoint): raise HTTPException(404, "找不到这台推送设备")
    return {"status": "disabled"}

@app.get("/api/family/reminders/unacknowledged")
def family_unacknowledged_reminders():
    require_role("family")
    return family_unacknowledged(request_identity().family_id, request_identity().member_id)

@app.post("/api/telemetry")
def record_usage_event(body: UsageEventCreate):
    require_role("elder")
    if body.conversation_id:
        with SessionLocal() as db:
            exists = db.scalar(select(Conversation.id).where(Conversation.id == body.conversation_id,
                Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id))
            if not exists: raise HTTPException(404, "找不到这段对话")
    with SessionLocal() as db:
        db.add(UsageEvent(id=str(uuid.uuid4()), family_id=request_identity().family_id, member_id=request_identity().member_id,
            conversation_id=body.conversation_id, session_key=body.session_key, event_type=body.event_type, metadata_json={}))
        db.commit()
    return {"status": "recorded"}

@app.get("/api/family/traces/{request_id}")
def get_request_trace(request_id: str):
    identity = request_identity()
    if identity.role not in {"family", "elder"}: raise HTTPException(404, "找不到这条请求记录")
    with SessionLocal() as db:
        root = db.scalar(select(RequestTrace).where(RequestTrace.id == request_id, RequestTrace.family_id == identity.family_id))
        if not root: raise HTTPException(404, "找不到这条请求记录")
        spans = db.scalars(select(TraceSpan).where(TraceSpan.request_id == request_id, TraceSpan.family_id == identity.family_id).order_by(TraceSpan.created_at)).all()
        return {"request_id": root.id, "route": root.route, "status_code": root.status_code, "started_at": root.started_at.isoformat(),
            "finished_at": root.finished_at.isoformat() if root.finished_at else None, "duration_ms": root.duration_ms,
            "spans": [{"name": span.span_name, "duration_ms": span.duration_ms, "outcome": span.outcome, "metadata": span.metadata_json, "created_at": span.created_at.isoformat()} for span in spans]}

@app.get("/api/family/usage")
def family_usage_report():
    require_role("family")
    family_id, member_id = request_identity().family_id, request_identity().member_id
    zone = ZoneInfo(family_timezone(family_id)); now_local = datetime.now(zone)
    week_start = (now_local - timedelta(days=now_local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        conversations = db.scalars(select(Conversation).where(Conversation.family_id == family_id, Conversation.member_id == member_id,
            Conversation.created_at >= week_start.astimezone(timezone.utc))).all()
        message_rows = db.scalars(select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(
            Message.family_id == family_id, Conversation.member_id == member_id, Message.role == "assistant",
            Message.created_at >= week_start.astimezone(timezone.utc))).all()
        week_voice = db.scalars(select(VoiceMetric).where(VoiceMetric.family_id == family_id,
            VoiceMetric.created_at >= week_start.astimezone(timezone.utc))).all()
        month_messages = db.scalars(select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(
            Message.family_id == family_id, Conversation.member_id == member_id, Message.role == "assistant",
            Message.created_at >= month_start.astimezone(timezone.utc))).all()
        month_voice = db.scalars(select(VoiceMetric).where(VoiceMetric.family_id == family_id,
            VoiceMetric.created_at >= month_start.astimezone(timezone.utc))).all()
        telemetry = db.scalars(select(UsageEvent).where(UsageEvent.family_id == family_id, UsageEvent.member_id == member_id,
            UsageEvent.created_at >= week_start.astimezone(timezone.utc))).all()
        confirmation_rows = db.scalars(select(Confirmation).where(Confirmation.family_id == family_id,
            Confirmation.created_at >= week_start.astimezone(timezone.utc))).all()
        reminder_rows = db.execute(select(ReminderDelivery, ReminderTrigger).join(ReminderTrigger, ReminderTrigger.id == ReminderDelivery.trigger_id)
            .join(Reminder, Reminder.id == ReminderTrigger.reminder_id).where(Reminder.family_id == family_id, Reminder.member_id == member_id,
                ReminderTrigger.triggered_at >= week_start.astimezone(timezone.utc))).all()
    sessions: dict[str, list[UsageEvent]] = {}
    for event in telemetry: sessions.setdefault(event.session_key, []).append(event)
    session_metrics = []
    for events in sessions.values():
        ordered_events = sorted(events, key=lambda event: event.created_at)
        last_event = ordered_events[-1]
        started_voice = any(event.event_type == "voice_start" for event in events)
        session_metrics.append({
            "date": ordered_events[0].created_at.astimezone(zone).date().isoformat(),
            "conversation_rounds": sum(event.event_type == "voice_complete" for event in events),
            "retry_count": sum(event.event_type == "retry" for event in events),
            "cancel_count": sum(event.event_type == "cancel" for event in events),
            "stt_empty_count": sum(event.event_type == "stt_empty" for event in events),
            "llm_timeout_count": sum(event.event_type == "llm_timeout" for event in events),
            "abandoned": started_voice and last_event.event_type not in {"voice_complete", "cancel"},
        })
    session_metrics.sort(key=lambda item: item["date"], reverse=True)
    opened = sum(1 for events in sessions.values() if any(event.event_type == "page_open" for event in events))
    started = sum(1 for events in sessions.values() if any(event.event_type == "voice_start" for event in events))
    abandoned = sum(1 for events in sessions.values() if any(event.event_type == "voice_start" for event in events)
        and max(events, key=lambda event: event.created_at).event_type not in {"voice_complete"})
    reminder_status = {key: 0 for key in ("acknowledged", "unacknowledged", "escalated", "ignored", "failed")}
    for delivery, _ in reminder_rows:
        if delivery.channel == "in_app":
            state = "unacknowledged" if delivery.status == "pending" else delivery.status
            if state in reminder_status: reminder_status[state] += 1
        if delivery.channel.startswith("web_push") and delivery.status == "failed": reminder_status["failed"] += 1
    def failed_tool_calls(rows):
        return sum(1 for message in rows for call in (message.tool_calls or []) if isinstance(call, dict) and call.get("ok") is False)
    def total_tool_calls(rows):
        return sum(1 for message in rows for call in (message.tool_calls or []) if isinstance(call, dict) and call.get("name"))
    def rate(numerator: int, denominator: int):
        return round(numerator / denominator, 3) if denominator else None
    def costs(messages, voices):
        input_tokens = sum(row.tokens_in or 0 for row in messages)
        output_tokens = sum(row.tokens_out or 0 for row in messages)
        stt_minutes = sum(row.audio_duration_ms or 0 for row in voices) / 60000
        tts_chars = sum(row.tts_characters or 0 for row in voices)
        total = stt_minutes * settings.cost_stt_per_minute_usd + input_tokens / 1_000_000 * settings.cost_input_per_million_tokens_usd + output_tokens / 1_000_000 * settings.cost_output_per_million_tokens_usd + tts_chars / 1_000_000 * settings.cost_tts_per_million_characters_usd
        return {"input_tokens": input_tokens, "output_tokens": output_tokens, "stt_minutes": round(stt_minutes, 2), "tts_characters": tts_chars, "estimated_usd": round(total, 4)}
    monthly_cost = costs(month_messages, month_voice)
    month_days = (now_local.date() - month_start.date()).days + 1
    daily_cost = []
    for offset in range(month_days):
        day = (month_start + timedelta(days=offset)).date()
        day_messages = [row for row in month_messages if row.created_at.astimezone(zone).date() == day]
        day_voice = [row for row in month_voice if row.created_at.astimezone(zone).date() == day]
        daily_cost.append({"date": day.isoformat(), **costs(day_messages, day_voice)})
    daily = []
    for offset in range(7):
        day = (week_start + timedelta(days=offset)).date()
        day_events = [event for event in telemetry if event.created_at.astimezone(zone).date() == day]
        day_sessions: dict[str, list[UsageEvent]] = {}
        for event in day_events: day_sessions.setdefault(event.session_key, []).append(event)
        day_open = sum(1 for events in day_sessions.values() if any(event.event_type == "page_open" for event in events))
        day_started = sum(1 for events in day_sessions.values() if any(event.event_type == "voice_start" for event in events))
        day_abandoned = sum(1 for events in day_sessions.values() if any(event.event_type == "voice_start" for event in events)
            and max(events, key=lambda event: event.created_at).event_type != "voice_complete")
        day_reminders = {key: 0 for key in reminder_status}
        day_reminder_total = 0
        for delivery, trigger in reminder_rows:
            if trigger.triggered_at.astimezone(zone).date() != day: continue
            if delivery.channel == "in_app":
                day_reminder_total += 1
                state = "unacknowledged" if delivery.status == "pending" else delivery.status
                if state in day_reminders: day_reminders[state] += 1
            if delivery.channel.startswith("web_push") and delivery.status == "failed": day_reminders["failed"] += 1
        day_confirmations = [row for row in confirmation_rows if row.created_at.astimezone(zone).date() == day]
        day_voice_starts = sum(event.event_type == "voice_start" for event in day_events)
        day_stt_empty = sum(event.event_type == "stt_empty" for event in day_events)
        day_llm_timeouts = sum(event.event_type == "llm_timeout" for event in day_events)
        day_message_rows = [row for row in message_rows if row.created_at.astimezone(zone).date() == day]
        day_failed_tools = failed_tool_calls(day_message_rows)
        day_total_tools = total_tool_calls(day_message_rows)
        day_final_confirmations = [row for row in day_confirmations if row.status in {"confirmed", "expired", "rejected", "needs_reconfirmation"}]
        day_confirmed = sum(row.status == "confirmed" for row in day_final_confirmations)
        day_timed_out = sum(row.status == "expired" for row in day_final_confirmations)
        daily.append({"date": day.isoformat(),
            "conversations": sum(1 for row in conversations if row.created_at.astimezone(zone).date() == day),
            "assistant_turns": sum(1 for row in message_rows if row.created_at.astimezone(zone).date() == day),
            "voice_turns": sum(1 for row in week_voice if row.created_at.astimezone(zone).date() == day),
            "page_open_sessions": day_open, "sessions_with_voice": day_started,
            "first_voice_rate": round(day_started / day_open, 3) if day_open else None,
            "stt_empty_rate": rate(day_stt_empty, day_voice_starts), "llm_timeout_rate": rate(day_llm_timeouts, day_voice_starts),
            "retry_count": sum(event.event_type == "retry" for event in day_events),
            "cancel_count": sum(event.event_type == "cancel" for event in day_events), "abandoned_sessions": day_abandoned,
            "stt_empty_count": day_stt_empty, "llm_timeout_count": day_llm_timeouts,
            "tool_failure_count": day_failed_tools, "tool_failure_rate": rate(day_failed_tools, day_total_tools),
            "confirmation_success_count": day_confirmed, "confirmation_success_rate": rate(day_confirmed, len(day_final_confirmations)),
            "confirmation_timeout_count": day_timed_out, "confirmation_timeout_rate": rate(day_timed_out, len(day_final_confirmations)),
            "reminder_unacknowledged_rate": rate(day_reminders["unacknowledged"] + day_reminders["escalated"], day_reminder_total),
            "reminders": day_reminders})
    final_confirmations = [row for row in confirmation_rows if row.status in {"confirmed", "expired", "rejected", "needs_reconfirmation"}]
    tool_failures = failed_tool_calls(message_rows)
    tool_total = total_tool_calls(message_rows)
    stt_empty = sum(event.event_type == "stt_empty" for event in telemetry)
    llm_timeouts = sum(event.event_type == "llm_timeout" for event in telemetry)
    voice_starts = sum(event.event_type == "voice_start" for event in telemetry)
    reminder_total = sum(1 for delivery, _ in reminder_rows if delivery.channel == "in_app")
    return {"week": {"daily": daily, "conversation_count": len(conversations), "assistant_turns": len(message_rows),
        "voice_turns": len(week_voice), "page_open_sessions": opened, "sessions_with_voice": started,
        "first_voice_rate": round(started / opened, 3) if opened else None, "retry_count": sum(e.event_type == "retry" for e in telemetry),
        "cancel_count": sum(e.event_type == "cancel" for e in telemetry), "abandoned_sessions": abandoned,
        "stt_empty_count": stt_empty, "stt_empty_rate": rate(stt_empty, voice_starts),
        "llm_timeout_count": llm_timeouts, "llm_timeout_rate": rate(llm_timeouts, voice_starts),
        "tool_failure_count": tool_failures, "tool_failure_rate": rate(tool_failures, tool_total),
        "confirmation_success_count": sum(row.status == "confirmed" for row in final_confirmations),
        "confirmation_success_rate": rate(sum(row.status == "confirmed" for row in final_confirmations), len(final_confirmations)),
        "confirmation_timeout_count": sum(row.status == "expired" for row in final_confirmations),
        "confirmation_timeout_rate": rate(sum(row.status == "expired" for row in final_confirmations), len(final_confirmations)),
        "reminder_unacknowledged_rate": rate(reminder_status["unacknowledged"] + reminder_status["escalated"], reminder_total),
        "reminders": reminder_status}, "session_metrics": session_metrics, "daily_cost": daily_cost, "monthly_cost": monthly_cost,
        "monthly_cost_limit_usd": settings.monthly_cost_limit_usd,
        "cost_alert": settings.monthly_cost_limit_usd > 0 and monthly_cost["estimated_usd"] >= settings.monthly_cost_limit_usd}

@app.post("/api/reminders/{reminder_id}/simulate")
def simulate_reminder(reminder_id: str):
    require_role("elder")
    if settings.app_env != "development": raise HTTPException(404, "Not found")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        reminder = db.scalar(select(Reminder).where(Reminder.id == reminder_id, Reminder.family_id == request_identity().family_id,
            Reminder.member_id == request_identity().member_id, Reminder.status == "scheduled").with_for_update())
        if not reminder: raise HTTPException(404, "找不到可模拟触发的提醒")
        reminder.next_trigger_at = now
        db.commit()
    from app.reminder_service import claim_due_batch
    claim_due_batch(now)
    return {"status": "triggered", **process_reminder_deliveries(now)}

@app.post("/api/reminders/simulate-next")
def simulate_next_reminder():
    require_role("elder")
    if settings.app_env != "development": raise HTTPException(404, "Not found")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        reminder = db.scalar(select(Reminder).where(Reminder.family_id == request_identity().family_id,
            Reminder.member_id == request_identity().member_id, Reminder.status == "scheduled").order_by(Reminder.next_trigger_at).with_for_update())
        if not reminder: raise HTTPException(404, "暂时没有可以模拟触发的提醒")
        reminder.next_trigger_at = now
        db.commit()
    from app.reminder_service import claim_due_batch
    claim_due_batch(now)
    return {"status": "triggered", **process_reminder_deliveries(now)}

@app.post("/api/voice/chat")
async def voice_chat(conversation_id: str = Form(...), audio: UploadFile = File(...)):
    require_role("elder")
    started = time.perf_counter()
    existing_confirmation = pending_confirmation(conversation_id)
    if existing_confirmation:
        return await confirmation_response(existing_confirmation, persist_message=False)
    with SessionLocal() as db:
        conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id))
        if not conv: raise HTTPException(status_code=404, detail="找不到这段对话")
    try:
        decode_started = time.perf_counter()
        wav, filename = await read_recording(audio)
        trace_event("audio_decode", int((time.perf_counter() - decode_started) * 1000), "ok", {"bytes": len(wav)})
        try:
            with wave.open(io.BytesIO(wav), "rb") as wav_reader:
                audio_duration_ms = round(wav_reader.getnframes() * 1000 / wav_reader.getframerate())
        except (wave.Error, ZeroDivisionError):
            audio_duration_ms = 0
        stt_started = time.perf_counter()
        try:
            transcript = await speech_provider.transcribe(wav, filename)
        except Exception:
            trace_event("stt", int((time.perf_counter() - stt_started) * 1000), "failed", {"provider": settings.stt_provider})
            raise
        stt_ms = int((time.perf_counter() - started) * 1000)
        stt_returned_at = time.perf_counter()
        trace_event("stt", stt_ms, "empty" if not transcript else "ok", {"provider": settings.stt_provider})
        if not transcript:
            record_internal_usage("stt_empty", conversation_id)
            return {"status": "not_heard", "message": "我没听清，您再说一遍好吗"}
        with SessionLocal() as db:
            conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id))
            role = db.get(CharacterRole, conv.role_id) if conv and conv.role_id else db.scalar(select(CharacterRole).where(CharacterRole.family_id == request_identity().family_id, CharacterRole.role_key == "daughter"))
            role_config = role.config if role else None
            role_id = role.id if role else None
            turn_number = int(db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id, Message.family_id == request_identity().family_id, Message.role == "user")) or 0) + 1
        user_message_id = str(uuid.uuid4())
        with SessionLocal() as db:
            db.add(Message(id=user_message_id, family_id=request_identity().family_id, conversation_id=conversation_id, role="user", content=transcript))
            conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id))
            if conv: conv.updated_at = datetime.now(timezone.utc)
            db.commit()
        memory_status = capture_explicit_memory(request_identity().family_id, request_identity().member_id, conversation_id, user_message_id, role_id, transcript)
        memories = relevant_memories(request_identity().family_id, request_identity().member_id, role_id)
        enabled_tools = role_allowed_tools(request_identity().family_id, role_id) if role_id else frozenset()
        timezone_name = family_timezone(request_identity().family_id)
        prompt = build_system_prompt(role_config, turn_number=turn_number, available_tools=descriptions_for_tools(enabled_tools), memories=memories, memory_instruction=memory_write_instruction(memory_status)) if role_config else "你是 MiniClaw 陪伴程序，不是真人或现实中的家人。"
        context = await prepare_context(conversation_id, prompt, transcript, exclude_message_id=user_message_id)
        graph = app.state.graph
        config = {"configurable": {"thread_id": conversation_id, "system_prompt": prompt, "summary": context.summary, "context_messages": context.raw_messages, "family_id": request_identity().family_id, "member_id": request_identity().member_id, "family_timezone": timezone_name, "allowed_tools": sorted(enabled_tools), "request_id": current_request_id()}}
        answer_parts: list[str] = []
        tool_audit: list[dict] = []
        first_token_ms = None
        llm_started = stt_returned_at
        agent_started = time.perf_counter()
        try:
            async with asyncio.timeout(15):
                async for event in graph.astream({"messages": [{"role": "user", "content": transcript}]}, config, stream_mode="custom"):
                    token = event.get("token", "")
                    if token:
                        if first_token_ms is None: first_token_ms = int((time.perf_counter() - llm_started) * 1000)
                        answer_parts.append(token)
                    if event.get("tool_call"):
                        tool_audit.append(event["tool_call"])
        except TimeoutError as exc:
            trace_event("agent", int((time.perf_counter() - agent_started) * 1000), "timeout", {"model": settings.chat_model})
            record_internal_usage("llm_timeout", conversation_id)
            raise HTTPException(status_code=504, detail="我想得有点久，咱们再说一次") from exc
        except Exception as exc:
            trace_event("agent", int((time.perf_counter() - agent_started) * 1000), "failed", {"error_type": type(exc).__name__, "model": settings.chat_model})
            raise
        trace_event("agent", int((time.perf_counter() - agent_started) * 1000), "ok", {"tool_count": len(tool_audit), "model": settings.chat_model})
        open_confirmation = pending_confirmation(conversation_id)
        if open_confirmation:
            return await confirmation_response(open_confirmation)
        answer = "".join(answer_parts)
        if not answer:
            raise HTTPException(502, "我想得有点久，咱们再说一次")
        tts_started = time.perf_counter()
        try:
            audio_out = await speech_provider.synthesize(answer)
        except Exception:
            trace_event("tts", int((time.perf_counter() - tts_started) * 1000), "failed", {"provider": settings.tts_provider})
            raise
        tts_ms = int((time.perf_counter() - tts_started) * 1000)
        trace_event("tts", tts_ms, "ok", {"characters": len(answer), "provider": settings.tts_provider})
        metric_id = str(uuid.uuid4())
        with SessionLocal() as db:
            db.add(Message(id=str(uuid.uuid4()), family_id=request_identity().family_id, conversation_id=conversation_id, role="assistant", content=answer, latency_ms=int((time.perf_counter()-started)*1000), model=settings.chat_model, used_tool=bool(tool_audit), tool_calls=tool_audit or None, tokens_in=context.input_tokens, tokens_out=count_tokens(answer), compressed=context.compressed, context_tokens=context.summary_tokens + context.raw_tokens))
            conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id))
            if conv: conv.updated_at = datetime.now(timezone.utc)
            db.add(VoiceMetric(id=metric_id, family_id=request_identity().family_id, conversation_id=conversation_id, stt_ms=stt_ms, llm_first_token_ms=first_token_ms or 0, tts_first_byte_ms=tts_ms, audio_duration_ms=audio_duration_ms, tts_characters=len(answer)))
            db.commit()
        return {"status": "ok", "metric_id": metric_id, "transcript": transcript, "answer": answer, "audio_base64": base64.b64encode(audio_out).decode("ascii"), "audio_content_type": "audio/mpeg"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="网络不太好，等一下再试") from exc

@app.get("/api/conversations/{conversation_id}/pending-confirmation")
def get_pending_confirmation(conversation_id: str):
    require_role("elder")
    with SessionLocal() as db:
        conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id))
        if not conv: raise HTTPException(404, "找不到这段对话")
    return pending_confirmation(conversation_id)

@app.post("/api/confirmations/{confirmation_id}/decision")
async def decide_confirmation(confirmation_id: str, body: ConfirmationDecision):
    require_role("elder")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.scalar(select(Confirmation).where(Confirmation.id == confirmation_id, Confirmation.family_id == request_identity().family_id, Confirmation.member_id == request_identity().member_id).with_for_update())
        if not row: raise HTTPException(404, "这次确认已经找不到了")
        if row.status not in {"pending", "resuming"}:
            raise HTTPException(409, "这次确认已经处理过了")
        if row.expires_at <= now:
            raise HTTPException(410, "确认时间到了，这次先不记")
        if row.status == "resuming" and row.resume_claimed_at and (now - row.resume_claimed_at).total_seconds() < 15:
            raise HTTPException(409, "正在处理，请稍等一下")
        row.status = "resuming"
        row.resume_claimed_at = now
        conversation_id = row.conversation_id
        db.commit()
    graph = app.state.graph
    graph_config = {"configurable": {"thread_id": conversation_id}}
    parts: list[str] = []
    latest_confirmation = None
    try:
        async for event in graph.astream(Command(resume={"confirmation_id": confirmation_id, "accepted": body.accepted, "trigger": "button"}), graph_config, stream_mode="custom"):
            if event.get("token"): parts.append(event["token"])
            if event.get("confirmation"): latest_confirmation = event["confirmation"]
    except Exception as exc:
        with SessionLocal() as db:
            row = db.get(Confirmation, confirmation_id)
            if row and row.status == "resuming":
                row.status = "pending"; row.resume_claimed_at = None; db.commit()
        raise HTTPException(503, "网络不太好，确认还在等您，请稍后再试") from exc
    still_pending = pending_confirmation(conversation_id)
    if still_pending:
        return await confirmation_response(still_pending)
    answer = "".join(parts) or ("好的，这次没有记录。" if not body.accepted else "好的，已经完成确认。")
    with SessionLocal() as db:
        db.add(Message(id=str(uuid.uuid4()), family_id=request_identity().family_id, conversation_id=conversation_id, role="assistant", content=answer))
        db.commit()
    audio_encoded = None
    try: audio_encoded = base64.b64encode(await speech_provider.synthesize(answer)).decode("ascii")
    except Exception: pass
    return {"status": "completed", "answer": answer, "audio_base64": audio_encoded, "audio_content_type": "audio/mpeg"}

@app.post("/api/voice/{metric_id}/playback")
def record_playback(metric_id: str, duration_ms: int = Form(...), end_to_end_ms: int | None = Form(None)):
    require_role("elder")
    with SessionLocal() as db:
        metric = db.scalar(select(VoiceMetric).where(VoiceMetric.id == metric_id, VoiceMetric.family_id == request_identity().family_id))
        if not metric: raise HTTPException(404, "找不到这次语音记录")
        metric.playback_ms = max(0, min(duration_ms, 600_000))
        if end_to_end_ms is not None:
            metric.end_to_end_ms = max(0, min(end_to_end_ms, 900_000))
        db.commit()
    return {"status": "ok"}

@app.get("/api/voice/metrics/daily")
def daily_voice_metrics(day: str | None = None):
    require_role("elder")
    target = datetime.fromisoformat(day).date() if day else datetime.now(timezone.utc).date()
    start = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(target, datetime.max.time(), tzinfo=timezone.utc)
    with SessionLocal() as db:
        rows = db.execute(select(VoiceMetric.stt_ms, VoiceMetric.llm_first_token_ms, VoiceMetric.tts_first_byte_ms, VoiceMetric.playback_ms, VoiceMetric.end_to_end_ms).where(VoiceMetric.family_id == request_identity().family_id, VoiceMetric.created_at >= start, VoiceMetric.created_at <= end)).all()
    def percentiles(values):
        ordered = sorted(values)
        if not ordered: return {"p50_ms": None, "p95_ms": None}
        return {"p50_ms": ordered[round((len(ordered)-1)*.50)], "p95_ms": ordered[round((len(ordered)-1)*.95)]}
    return {"date": target.isoformat(), "samples": len(rows), "segments": {name: percentiles([getattr(row, field) for row in rows if getattr(row, field) is not None]) for name, field in (("recording_end_to_stt_return", "stt_ms"), ("stt_return_to_llm_first_token", "llm_first_token_ms"), ("llm_answer_to_tts_playable", "tts_first_byte_ms"), ("tts_playable_to_playback_end", "playback_ms"), ("recording_end_to_playback_end", "end_to_end_ms"))}}

def _reminders_for_range(start: datetime, end: datetime):
    with SessionLocal() as db:
        rows = db.scalars(select(Reminder).where(Reminder.family_id == request_identity().family_id, Reminder.member_id == request_identity().member_id, Reminder.status == "scheduled", Reminder.next_trigger_at >= start.astimezone(timezone.utc), Reminder.next_trigger_at < end.astimezone(timezone.utc)).order_by(Reminder.next_trigger_at)).all()
        return [{"id": row.id, "title": row.title, "due_at": row.next_trigger_at.isoformat(), "timezone": row.timezone_name, "recurrence": row.recurrence, "version": row.version} for row in rows]

@app.get("/api/reminders/today")
def reminders_today():
    require_role("elder")
    zone = ZoneInfo(settings.family_timezone)
    now = datetime.now(zone)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {"items": _reminders_for_range(start, start + timedelta(days=1))}

@app.get("/api/reminders/week")
def reminders_this_week():
    require_role("elder")
    zone = ZoneInfo(settings.family_timezone)
    now = datetime.now(zone)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"items": _reminders_for_range(start, start + timedelta(days=7))}

@app.get("/api/reminders/unacknowledged")
def unacknowledged_reminders():
    require_role("elder")
    with SessionLocal() as db:
        rows = db.execute(select(Reminder, ReminderTrigger, ReminderDelivery).join(ReminderTrigger, ReminderTrigger.reminder_id == Reminder.id).join(ReminderDelivery, ReminderDelivery.trigger_id == ReminderTrigger.id).where(Reminder.family_id == request_identity().family_id, Reminder.member_id == request_identity().member_id, ReminderDelivery.channel == "in_app", ReminderDelivery.status.in_(["pending", "unacknowledged", "escalated"])).order_by(ReminderTrigger.scheduled_for)).all()
        return {"items": [{"delivery_id": delivery.id, "reminder_id": reminder.id, "title": reminder.title, "scheduled_for": trigger.scheduled_for.isoformat(), "triggered_at": trigger.triggered_at.isoformat(), "status": delivery.status, "is_catch_up": trigger.is_catch_up} for reminder, trigger, delivery in rows]}

@app.post("/api/reminders/deliveries/{delivery_id}/acknowledge")
def acknowledge_reminder_delivery(delivery_id: str):
    require_role("elder")
    if not acknowledge_delivery(delivery_id, request_identity().family_id, request_identity().member_id): raise HTTPException(404, "找不到这条提醒投递")
    return {"status": "acknowledged"}

@app.post("/api/reminders/deliveries/{delivery_id}/ignore")
def ignore_reminder_delivery(delivery_id: str):
    require_role("elder")
    if not ignore_delivery(delivery_id, request_identity().family_id, request_identity().member_id): raise HTTPException(404, "找不到这条提醒投递")
    return {"status": "ignored"}

@app.get("/api/memories")
def get_memories():
    require_role("elder")
    return {"items": list_member_memories(request_identity().family_id, request_identity().member_id)}

@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str):
    require_role("elder")
    if not soft_delete_memory(memory_id, request_identity().family_id, request_identity().member_id): raise HTTPException(404, "找不到这条记忆")
    return {"status": "deleted"}

@app.delete("/api/memories/role/{role_key}")
def delete_role_memories(role_key: str):
    require_role("elder")
    with SessionLocal() as db:
        role = db.scalar(select(CharacterRole).where(CharacterRole.family_id == request_identity().family_id, CharacterRole.role_key == role_key))
        if not role: raise HTTPException(404, "找不到这个角色")
    return {"status": "deleted", "count": soft_delete_role_memories(role.id, request_identity().family_id, request_identity().member_id)}

@app.post("/api/memories/revoke-shared")
def revoke_shared_memory_access():
    require_role("elder")
    return {"status": "revoked", "count": revoke_shared_memories(request_identity().family_id, request_identity().member_id)}

@app.get("/api/debug/conversations/{conversation_id}/context")
def debug_context(conversation_id: str):
    require_role("elder")
    if settings.app_env != "development": raise HTTPException(404, "Not found")
    with SessionLocal() as db:
        conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id))
        if not conv: raise HTTPException(404, "找不到这段对话")
        role = db.get(CharacterRole, conv.role_id) if conv.role_id else db.scalar(select(CharacterRole).where(CharacterRole.family_id == request_identity().family_id, CharacterRole.role_key == "daughter"))
        summary = db.scalar(select(ConversationSummary).where(ConversationSummary.conversation_id == conversation_id, ConversationSummary.family_id == request_identity().family_id))
        rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.family_id == request_identity().family_id).order_by(Message.created_at, Message.id)).all()
    memories = relevant_memories(request_identity().family_id, request_identity().member_id, role.id if role else None)
    enabled_tools = role_allowed_tools(request_identity().family_id, role.id) if role else frozenset()
    prompt = build_system_prompt(role.config, available_tools=descriptions_for_tools(enabled_tools), memories=memories) if role else "你是 MiniClaw 陪伴程序。"
    history = [{"id": row.id, "role": row.role, "content": row.content} for row in rows]
    if summary and summary.covered_through_message_id:
        index = next((i for i, item in enumerate(history) if item["id"] == summary.covered_through_message_id), -1)
        history = history[index + 1:]
    tools = [call for row in rows if row.tool_calls for call in row.tool_calls]
    tool_result_tokens = sum(count_tokens(json.dumps(call.get("result", {}), ensure_ascii=False)) for call in tools)
    raw = [{"role": row["role"], "content": row["content"], "tokens": count_tokens(row["content"])} for row in history]
    summary_text = summary.summary if summary else ""
    return {"conversation_id": conversation_id, "system_prompt": prompt, "system_prompt_tokens": count_tokens(prompt), "summary": summary_text, "summary_tokens": count_tokens(summary_text), "raw_messages": raw, "raw_tokens": sum(item["tokens"] for item in raw), "tool_results": tools, "tool_result_tokens": tool_result_tokens, "budget_tokens": settings.context_token_budget}

@app.get("/api/roles")
def list_roles():
    with SessionLocal() as db:
        roles = db.scalars(select(CharacterRole).where(CharacterRole.family_id == request_identity().family_id).order_by(CharacterRole.role_key)).all()
        return [{"role_key": role.role_key, **role.config, "version": role.version} for role in roles]

@app.post("/api/roles/import")
def import_roles(body: RoleImport):
    require_role("family")
    try:
        document = yaml.safe_load(body.yaml_text)
        if not isinstance(document, dict) or not isinstance(document.get("roles"), list) or len(document["roles"]) != 2:
            raise ValueError("配置文件必须包含女儿和孙女两个角色")
        imported = {}
        for item in document["roles"]:
            role_key = item.pop("role_key", None)
            if role_key not in {"daughter", "granddaughter"} or role_key in imported:
                raise ValueError("仅支持 daughter 和 granddaughter 两个角色，且不能重复")
            imported[role_key] = validate_role_config(item)
        if set(imported) != {"daughter", "granddaughter"}:
            raise ValueError("必须同时提供女儿和孙女角色")
    except (yaml.YAMLError, ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(422, f"角色配置无法导入：{exc}") from exc
    with SessionLocal() as db:
        for key, config in imported.items():
            row = db.scalar(select(CharacterRole).where(CharacterRole.family_id == request_identity().family_id, CharacterRole.role_key == key))
            if row:
                before = row.config
                row.config = config; row.version += 1
                add_audit(db, request_identity().family_id, request_identity().display_name, "role", row.id, "updated", before, config, f"{config['name']}角色配置已调整。")
            else:
                role_id = str(uuid.uuid4())
                db.add(CharacterRole(id=role_id, family_id=request_identity().family_id, role_key=key, config=config, version=1))
                add_audit(db, request_identity().family_id, request_identity().display_name, "role", role_id, "created", None, config, f"{config['name']}角色已设置。")
        db.commit()
    return {"status": "ok", "updated": sorted(imported)}

@app.get("/api/family/roles")
def family_roles():
    require_role("family")
    return {"items": list_roles()}

@app.put("/api/family/roles/{role_key}")
def family_update_role(role_key: str, body: RoleConfigUpdate):
    require_role("family")
    try:
        version = update_role(request_identity().family_id, request_identity().display_name, role_key, body.expected_version, body.config)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"status": "updated", "role_key": role_key, "version": version}

@app.get("/api/family/facts")
def family_facts():
    require_role("family")
    with SessionLocal() as db:
        rows = db.scalars(select(FamilyFact).where(FamilyFact.family_id == request_identity().family_id).order_by(FamilyFact.created_at.desc())).all()
        return {"items": [{"id": row.id, "fact_type": row.fact_type, "content": row.content, "authorized": row.authorized, "created_at": row.created_at.isoformat()} for row in rows]}

@app.post("/api/family/facts")
def family_create_fact(body: FamilyFactCreate):
    require_role("family")
    fact_id = add_family_fact(request_identity().family_id, request_identity().display_name, body.fact_type, body.content, body.authorized)
    return {"status": "created", "id": fact_id}

@app.delete("/api/family/facts/{fact_id}")
def family_remove_fact(fact_id: str):
    require_role("family")
    if not delete_family_fact(request_identity().family_id, request_identity().display_name, fact_id): raise HTTPException(404, "找不到这条家庭资料")
    return {"status": "deleted"}

@app.put("/api/family/facts/{fact_id}")
def family_update_fact(fact_id: str, body: FamilyFactCreate):
    require_role("family")
    if not update_family_fact(request_identity().family_id, request_identity().display_name, fact_id, body.fact_type, body.content, body.authorized): raise HTTPException(404, "找不到这条家庭资料")
    return {"status": "updated"}

@app.get("/api/family/reminders")
def family_reminders():
    require_role("family")
    with SessionLocal() as db:
        rows = db.scalars(select(Reminder).where(Reminder.family_id == request_identity().family_id, Reminder.member_id == request_identity().member_id, Reminder.status.in_(["scheduled", "paused", "triggered", "needs_reconfirmation"])).order_by(Reminder.due_at.desc())).all()
        return {"items": [{"id": row.id, "title": row.title, "due_at": (row.next_trigger_at or row.due_at).isoformat(), "timezone": row.timezone_name, "recurrence": row.recurrence, "status": row.status} for row in rows]}

@app.get("/api/family/memories")
def family_shared_memories():
    require_role("family")
    return {"items": shared_memories(request_identity().family_id, request_identity().member_id)}

@app.delete("/api/family/memories/{memory_id}")
def family_remove_shared_memory(memory_id: str):
    require_role("family")
    if not delete_shared_memory(request_identity().family_id, request_identity().member_id, request_identity().display_name, memory_id): raise HTTPException(404, "找不到已授权的共享记忆")
    return {"status": "deleted"}

@app.post("/api/family/memories/revoke")
def family_revoke_shared(body: DestructiveConfirmation):
    require_role("family")
    if body.confirmation_phrase != "确认撤回": raise HTTPException(422, "请明确确认撤回共享记忆授权")
    count = revoke_shared_memories(request_identity().family_id, request_identity().member_id)
    with SessionLocal() as db:
        add_audit(db, request_identity().family_id, request_identity().display_name, "shared_memory_access", request_identity().family_id, "revoked", {"authorized": True}, {"authorized": False}, "家庭共享记忆授权已撤回。")
        db.commit()
    return {"status": "revoked", "count": count}

@app.get("/api/family/export")
def family_export():
    require_role("family")
    data = export_family_data(request_identity().family_id, request_identity().member_id)
    return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=miniclaw-family-export.json"})

@app.post("/api/family/data/delete")
async def family_delete_all_data(body: DestructiveConfirmation):
    require_role("family")
    if body.confirmation_phrase != "确认删除": raise HTTPException(422, "请明确确认删除家庭数据")
    with SessionLocal() as db:
        conversation_ids = db.scalars(select(Conversation.id).where(Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id)).all()
    removed = delete_family_data(request_identity().family_id, request_identity().member_id, request_identity().display_name)
    for conversation_id in conversation_ids:
        try: await app.state.graph.checkpointer.adelete_thread(conversation_id)
        except Exception as exc: logger.warning("Could not remove checkpoint for deleted conversation: %s", type(exc).__name__)
    return {"status": "deleted", "conversations_removed": removed}

@app.get("/api/family/changes/latest")
def latest_family_change():
    require_role("elder")
    with SessionLocal() as db:
        row = db.scalar(select(FamilyChangeAudit).where(FamilyChangeAudit.family_id == request_identity().family_id).order_by(FamilyChangeAudit.created_at.desc()).limit(1))
        if not row: return None
        return {"id": row.id, "summary": f"{row.actor}：{row.elder_summary}", "created_at": row.created_at.isoformat()}

@app.get("/api/family/audit")
def family_audit_log():
    require_role("family")
    with SessionLocal() as db:
        rows = db.scalars(select(FamilyChangeAudit).where(FamilyChangeAudit.family_id == request_identity().family_id).order_by(FamilyChangeAudit.created_at.desc()).limit(100)).all()
        return {"items": [{"actor": row.actor, "entity_type": row.entity_type, "action": row.action, "before": row.before_value, "after": row.after_value, "created_at": row.created_at.isoformat()} for row in rows]}

@app.post("/api/conversations/{conversation_id}/role")
async def switch_role(conversation_id: str, body: RoleSelection):
    require_role("elder")
    with SessionLocal() as db:
        conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id))
        if not conv: raise HTTPException(404, "找不到这段对话")
        role = db.scalar(select(CharacterRole).where(CharacterRole.family_id == request_identity().family_id, CharacterRole.role_key == body.role_key))
        if not role: raise HTTPException(404, "这个陪伴角色暂时不可用")
        previous = db.get(CharacterRole, conv.role_id) if conv.role_id else None
        conv.role_id = role.id
        config = role.config
        db.add(RoleSwitch(id=str(uuid.uuid4()), family_id=request_identity().family_id, member_id=request_identity().member_id, conversation_id=conversation_id, from_role=previous.role_key if previous else None, to_role=role.role_key))
        db.add(Message(id=str(uuid.uuid4()), family_id=request_identity().family_id, conversation_id=conversation_id, role="assistant", content=config["self_intro"], used_tool=False))
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
    try:
        audio = await speech_provider.synthesize(config["self_intro"])
        audio_encoded = base64.b64encode(audio).decode("ascii")
    except Exception:
        audio_encoded = None
    return {"role_key": body.role_key, "name": config["name"], "self_intro": config["self_intro"], "audio_base64": audio_encoded, "audio_content_type": "audio/mpeg"}

@app.get("/api/conversations")
def list_conversations():
    require_role("elder")
    with SessionLocal() as db:
        rows = db.scalars(select(Conversation).where(Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id).order_by(Conversation.updated_at.desc())).all()
        roles = {role.id: role.role_key for role in db.scalars(select(CharacterRole).where(CharacterRole.family_id == request_identity().family_id)).all()}
        return [{"id": row.id, "title": row.title, "role_key": roles.get(row.role_id), "updated_at": row.updated_at.isoformat()} for row in rows]

@app.post("/api/conversations")
def create_conversation():
    require_role("elder")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = Conversation(id=str(uuid.uuid4()), family_id=request_identity().family_id, member_id=request_identity().member_id, title="新对话", created_at=now, updated_at=now)
        db.add(row); db.commit()
    return {"id": row.id, "title": row.title, "role_key": None, "updated_at": now.isoformat()}

@app.get("/api/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str):
    require_role("elder")
    with SessionLocal() as db:
        conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id))
        if not conversation: raise HTTPException(status_code=404, detail="找不到这段对话")
        rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.family_id == request_identity().family_id).order_by(Message.created_at)).all()
        return [{"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()} for m in rows]

@app.post("/api/conversations/{conversation_id}/chat")
async def chat(conversation_id: str, body: ChatRequest):
    require_role("elder")
    identity = request_identity()
    request_id = current_request_id()
    request_started = time.perf_counter()
    with SessionLocal() as db:
        conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id, Conversation.member_id == request_identity().member_id))
        if not conv: raise HTTPException(status_code=404, detail="找不到这段对话")
        role = db.get(CharacterRole, conv.role_id) if conv.role_id else db.scalar(select(CharacterRole).where(CharacterRole.family_id == request_identity().family_id, CharacterRole.role_key == "daughter"))
        turn_number = int(db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id, Message.family_id == request_identity().family_id, Message.role == "user")) or 0) + 1
    existing_confirmation = pending_confirmation(conversation_id)
    if existing_confirmation:
        async def confirmation_events():
            identity_token = set_identity(identity)
            try:
                result = await confirmation_response(existing_confirmation, persist_message=False)
                yield "data: " + json.dumps({"type": "confirmation", "content": result}, ensure_ascii=False) + "\n\n"
            finally:
                reset_identity(identity_token)
        return StreamingResponse(confirmation_events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    user_message_id = str(uuid.uuid4())
    with SessionLocal() as db:
        user_message = Message(id=user_message_id, family_id=request_identity().family_id, conversation_id=conversation_id, role="user", content=body.content)
        db.add(user_message); db.commit()
        conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id))
        if conv: conv.updated_at = datetime.now(timezone.utc)
        db.commit()
    role_id = role.id if role else None
    memory_status = capture_explicit_memory(request_identity().family_id, request_identity().member_id, conversation_id, user_message_id, role_id, body.content)
    memories = relevant_memories(request_identity().family_id, request_identity().member_id, role_id)
    enabled_tools = role_allowed_tools(request_identity().family_id, role_id) if role_id else frozenset()
    timezone_name = family_timezone(request_identity().family_id)
    prompt = build_system_prompt(role.config, turn_number=turn_number, available_tools=descriptions_for_tools(enabled_tools), memories=memories, memory_instruction=memory_write_instruction(memory_status)) if role else "你是 MiniClaw 陪伴程序，不是真人或现实中的家人。"
    context = await prepare_context(conversation_id, prompt, body.content, exclude_message_id=user_message_id)
    async def events():
        identity_token = set_identity(identity)
        trace_token = set_request_id(request_id) if request_id else None
        started = time.perf_counter(); parts = []; tool_audit = []; stream_status = 200
        try:
            graph = app.state.graph
            agent_started = time.perf_counter()
            config = {"configurable": {"thread_id": conversation_id, "system_prompt": prompt, "summary": context.summary, "context_messages": context.raw_messages, "family_id": request_identity().family_id, "member_id": request_identity().member_id, "family_timezone": timezone_name, "allowed_tools": sorted(enabled_tools), "request_id": request_id}}
            async for event in graph.astream({"messages": [{"role": "user", "content": body.content}]}, config, stream_mode="custom"):
                token = event.get("token", "")
                if token:
                    parts.append(token)
                    yield "data: " + json.dumps({"type": "token", "content": token}, ensure_ascii=False) + "\n\n"
                if event.get("tool_call"):
                    tool_audit.append(event["tool_call"])
            trace_event("agent", int((time.perf_counter() - agent_started) * 1000), "ok", {"tool_count": len(tool_audit), "model": settings.chat_model})
            open_confirmation = pending_confirmation(conversation_id)
            if open_confirmation:
                result = await confirmation_response(open_confirmation)
                yield "data: " + json.dumps({"type": "confirmation", "content": result}, ensure_ascii=False) + "\n\n"
                return
            answer = "".join(parts)
            with SessionLocal() as db:
                db.add(Message(id=str(uuid.uuid4()), family_id=request_identity().family_id, conversation_id=conversation_id, role="assistant", content=answer, latency_ms=int((time.perf_counter()-started)*1000), model=settings.chat_model, used_tool=bool(tool_audit), tool_calls=tool_audit or None, tokens_in=context.input_tokens, tokens_out=count_tokens(answer), compressed=context.compressed, context_tokens=context.summary_tokens + context.raw_tokens))
                conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.family_id == request_identity().family_id))
                if conv: conv.updated_at = datetime.now(timezone.utc)
                db.commit()
            yield "data: " + json.dumps({"type": "done", "content": ""}) + "\n\n"
        except Exception as exc:
            stream_status = 500
            trace_event("agent_stream", int((time.perf_counter() - started) * 1000), "failed", {"error_type": type(exc).__name__})
            yield "data: " + json.dumps({"type": "error", "content": "连接暂时不太顺畅，请稍后再试。"}, ensure_ascii=False) + "\n\n"
        finally:
            if request_id: trace_finish(request_id, stream_status, int((time.perf_counter() - request_started) * 1000))
            if trace_token is not None: reset_request_id(trace_token)
            reset_identity(identity_token)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})
