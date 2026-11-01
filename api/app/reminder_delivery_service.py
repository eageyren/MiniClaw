"""Idempotent Web Push delivery, retries, and reminder escalation."""
import hashlib
import ipaddress
import json
import logging
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from pywebpush import WebPushException, webpush
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import DeviceCredential, FamilyAccess, PushSubscription, Reminder, ReminderDelivery, ReminderTrigger
from app.observability import trace_event

logger = logging.getLogger("miniclaw.delivery")
ALLOWED_PUSH_HOSTS = (".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com", ".fcm.googleapis.com")
RETRY_MINUTES = (1, 3, 7)

def _valid_endpoint(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        return settings.app_env == "development" and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global: return False
    except ValueError:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
            if any(not ipaddress.ip_address(address).is_global for address in addresses): return False
        except OSError:
            return False
    return host == "fcm.googleapis.com" or any(host.endswith(suffix) for suffix in ALLOWED_PUSH_HOSTS)

def upsert_subscription(identity, data: dict) -> dict:
    if not _valid_endpoint(data["endpoint"]):
        raise ValueError("推送服务地址无效，请重新开启通知。")
    keys = data.get("keys") or {}
    p256dh, auth_key = keys.get("p256dh"), keys.get("auth")
    if not p256dh or not auth_key: raise ValueError("推送密钥不完整，请重新开启通知。")
    if identity.role == "elder" and not identity.device_id: raise ValueError("长辈设备还没有完成配对。")
    if identity.role == "family" and not identity.family_account_id: raise ValueError("家属登录已经失效，请重新登录。")
    endpoint_hash = hashlib.sha256(data["endpoint"].encode()).hexdigest()
    with SessionLocal() as db:
        row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash).with_for_update())
        if row and (row.family_id != identity.family_id or row.member_id != identity.member_id or row.actor_role != identity.role or row.device_id != identity.device_id or row.family_account_id != identity.family_account_id):
            raise ValueError("这个推送设备已绑定其他家庭。")
        if not row:
            row = PushSubscription(id=str(uuid.uuid4()), family_id=identity.family_id, member_id=identity.member_id,
                actor_role=identity.role, device_id=identity.device_id, family_account_id=identity.family_account_id,
                endpoint=data["endpoint"], endpoint_hash=endpoint_hash, p256dh=p256dh, auth_key=auth_key,
                notify_enabled=(identity.role == "family" and bool(data.get("notify_family"))))
            db.add(row)
        else:
            row.endpoint = data["endpoint"]; row.p256dh = p256dh; row.auth_key = auth_key
            row.enabled = True; row.failure_count = 0; row.last_error = None
            if identity.role == "family": row.notify_enabled = bool(data.get("notify_family"))
        db.commit()
        return {"id": row.id, "enabled": row.enabled, "notify_enabled": row.notify_enabled}

def disable_subscription(identity, endpoint: str) -> bool:
    endpoint_hash = hashlib.sha256(endpoint.encode()).hexdigest()
    with SessionLocal() as db:
        row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash,
            PushSubscription.family_id == identity.family_id, PushSubscription.member_id == identity.member_id,
            PushSubscription.actor_role == identity.role,
            (PushSubscription.device_id == identity.device_id if identity.role == "elder" else PushSubscription.family_account_id == identity.family_account_id)))
        if not row: return False
        row.enabled = False
        db.commit()
        return True

def subscription_settings(identity) -> dict:
    query = select(PushSubscription).where(PushSubscription.family_id == identity.family_id,
        PushSubscription.member_id == identity.member_id, PushSubscription.actor_role == identity.role)
    if identity.role == "elder": query = query.where(PushSubscription.device_id == identity.device_id)
    else: query = query.where(PushSubscription.family_account_id == identity.family_account_id)
    with SessionLocal() as db:
        row = db.scalar(query.order_by(PushSubscription.updated_at.desc()).limit(1))
        return {"enabled": bool(row and row.enabled), "notify_enabled": bool(row and row.notify_enabled)}

def _queue_pushes(db, reminder: Reminder, trigger: ReminderTrigger, now: datetime, family: bool = False, repeat: bool = False) -> int:
    role = "family" if family else "elder"
    query = select(PushSubscription).where(PushSubscription.family_id == reminder.family_id,
        PushSubscription.member_id == reminder.member_id, PushSubscription.actor_role == role,
        PushSubscription.enabled.is_(True))
    if family:
        query = query.where(PushSubscription.notify_enabled.is_(True),
            PushSubscription.family_account_id.in_(select(FamilyAccess.family_account_id).where(FamilyAccess.family_id == reminder.family_id, FamilyAccess.member_id == reminder.member_id, FamilyAccess.revoked_at.is_(None))))
    else:
        query = query.join(DeviceCredential, DeviceCredential.id == PushSubscription.device_id).where(DeviceCredential.revoked_at.is_(None), DeviceCredential.expires_at > now)
    subscriptions = db.scalars(query).all()
    for subscription in subscriptions:
        channel = f"web_push_{role}{'_repeat' if repeat else ''}"
        key = f"{channel}:{trigger.id}:{subscription.id}"
        if db.scalar(select(ReminderDelivery.id).where(ReminderDelivery.idempotency_key == key)):
            continue
        db.add(ReminderDelivery(id=str(uuid.uuid4()), trigger_id=trigger.id, channel=channel, status="pending",
            idempotency_key=key, target_subscription_id=subscription.id, next_attempt_at=now, status_updated_at=now))
    return len(subscriptions)

def _queue_due_and_escalate(db, now: datetime) -> int:
    queued = 0
    due_triggers = db.scalars(select(ReminderTrigger).join(Reminder, Reminder.id == ReminderTrigger.reminder_id)
        .where(ReminderTrigger.triggered_at <= now,
            ~select(ReminderDelivery.id).where(ReminderDelivery.trigger_id == ReminderTrigger.id, ReminderDelivery.channel == "web_push_elder").exists())
        .order_by(ReminderTrigger.triggered_at.desc()).limit(500)).all()
    for trigger in due_triggers:
        reminder = db.get(Reminder, trigger.reminder_id)
        if reminder: queued += _queue_pushes(db, reminder, trigger, now)
    rows = db.execute(select(ReminderDelivery, ReminderTrigger, Reminder).join(ReminderTrigger, ReminderTrigger.id == ReminderDelivery.trigger_id)
        .join(Reminder, Reminder.id == ReminderTrigger.reminder_id).where(ReminderDelivery.channel == "in_app",
            ReminderDelivery.status.in_(["pending", "unacknowledged"]), ReminderTrigger.triggered_at <= now - timedelta(minutes=5))).all()
    for delivery, trigger, reminder in rows:
        age = now - trigger.triggered_at
        if age >= timedelta(minutes=5):
            _queue_pushes(db, reminder, trigger, now, repeat=True)
        if age >= timedelta(minutes=15):
            if delivery.status != "escalated":
                delivery.status = "escalated"
                delivery.status_updated_at = now
                _queue_pushes(db, reminder, trigger, now, family=True)
        elif delivery.status == "pending":
            delivery.status = "unacknowledged"
            delivery.status_updated_at = now
    return queued

def _send_delivery(db, delivery: ReminderDelivery, now: datetime) -> None:
    if delivery.status not in {"pending", "retry"} or (delivery.next_attempt_at and delivery.next_attempt_at > now): return
    trigger = db.get(ReminderTrigger, delivery.trigger_id)
    reminder = db.get(Reminder, trigger.reminder_id) if trigger else None
    subscription = db.get(PushSubscription, delivery.target_subscription_id) if delivery.target_subscription_id else None
    if not trigger or not reminder or not subscription or not subscription.enabled:
        delivery.status = "failed"; delivery.error_code = "SUBSCRIPTION_UNAVAILABLE"; return
    if subscription.actor_role == "elder":
        device = db.get(DeviceCredential, subscription.device_id) if subscription.device_id else None
        if not device or device.revoked_at or device.expires_at <= now:
            delivery.status = "ignored"; return
    else:
        link = db.scalar(select(FamilyAccess.id).where(FamilyAccess.family_account_id == subscription.family_account_id,
            FamilyAccess.family_id == reminder.family_id, FamilyAccess.member_id == reminder.member_id, FamilyAccess.revoked_at.is_(None)))
        if not link:
            delivery.status = "ignored"; return
    in_app = db.scalar(select(ReminderDelivery).where(ReminderDelivery.trigger_id == trigger.id, ReminderDelivery.channel == "in_app"))
    if in_app and in_app.status in {"acknowledged", "ignored"}:
        delivery.status = "ignored"; return
    if not settings.push_vapid_private_key or not settings.push_vapid_public_key:
        delivery.status = "failed"; delivery.error_code = "PUSH_NOT_CONFIGURED"; subscription.last_error = "PUSH_NOT_CONFIGURED"; return
    try:
        payload = {"title": "MiniClaw 提醒", "body": reminder.title, "url": "/", "delivery_id": delivery.id, "reminder_id": reminder.id}
        webpush(subscription_info={"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth_key}},
            data=json.dumps(payload, ensure_ascii=False), vapid_private_key=settings.push_vapid_private_key,
            vapid_claims={"sub": settings.push_vapid_subject}, ttl=3600)
        delivery.attempt_count += 1; delivery.attempted_at = now; delivery.status = "sent"; delivery.delivered_at = now
        subscription.failure_count = 0; subscription.last_error = None
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        delivery.attempt_count += 1; delivery.attempted_at = now
        subscription.failure_count += 1; subscription.last_error = f"PUSH_{status or 'ERROR'}"
        delivery.error_code = subscription.last_error
        if status in {404, 410}:
            subscription.enabled = False; subscription.last_error = "DEVICE_UNREACHABLE"; delivery.status = "failed"
        elif delivery.attempt_count <= len(RETRY_MINUTES):
            delivery.status = "retry"; delivery.next_attempt_at = now + timedelta(minutes=RETRY_MINUTES[delivery.attempt_count - 1])
        else:
            delivery.status = "failed"
            if subscription.failure_count >= 3:
                subscription.enabled = False; subscription.last_error = "DEVICE_UNREACHABLE"
        logger.info("Push delivery failed", extra={"family_id": reminder.family_id, "channel": delivery.channel, "error_code": delivery.error_code})
    except Exception as exc:
        delivery.attempt_count += 1; delivery.attempted_at = now; delivery.error_code = type(exc).__name__[:80]
        subscription.failure_count += 1
        subscription.last_error = f"PUSH_{type(exc).__name__[:60]}"
        if delivery.attempt_count <= len(RETRY_MINUTES):
            delivery.status = "retry"; delivery.next_attempt_at = now + timedelta(minutes=RETRY_MINUTES[delivery.attempt_count - 1])
        else:
            delivery.status = "failed"
            if subscription.failure_count >= 3:
                subscription.enabled = False; subscription.last_error = "DEVICE_UNREACHABLE"

def process_reminder_deliveries(now: datetime | None = None, limit: int = 100) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None: now = now.replace(tzinfo=timezone.utc)
    sent = 0
    with SessionLocal() as db:
        queued = _queue_due_and_escalate(db, now)
        deliveries = db.scalars(select(ReminderDelivery).where(ReminderDelivery.channel != "in_app",
            ReminderDelivery.status.in_(["pending", "retry"]), (ReminderDelivery.next_attempt_at.is_(None) | (ReminderDelivery.next_attempt_at <= now)))
            .order_by(ReminderDelivery.attempted_at).limit(limit).with_for_update(skip_locked=True)).all()
        for delivery in deliveries:
            old_status = delivery.status
            started = time.perf_counter()
            _send_delivery(db, delivery, now)
            delivery.status_updated_at = now
            reminder_trigger = db.get(ReminderTrigger, delivery.trigger_id)
            reminder = db.get(Reminder, reminder_trigger.reminder_id) if reminder_trigger else None
            trace_event("reminder_delivery", int((time.perf_counter() - started) * 1000), delivery.status,
                {"delivery_id": delivery.id, "channel": delivery.channel, "attempt": delivery.attempt_count},
                request_id=reminder.origin_request_id if reminder else None, family_id=reminder.family_id if reminder else None)
            if old_status in {"pending", "retry"} and delivery.status == "sent": sent += 1
        db.commit()
    return {"queued": queued, "sent": sent}

def family_unacknowledged(family_id: str, member_id: str) -> dict:
    with SessionLocal() as db:
        rows = db.execute(select(Reminder, ReminderTrigger, ReminderDelivery).join(ReminderTrigger, ReminderTrigger.reminder_id == Reminder.id)
            .join(ReminderDelivery, ReminderDelivery.trigger_id == ReminderTrigger.id).where(Reminder.family_id == family_id,
                Reminder.member_id == member_id, ReminderDelivery.channel == "in_app", ReminderDelivery.status.in_(["unacknowledged", "escalated"]))
            .order_by(ReminderTrigger.triggered_at.desc()).limit(100)).all()
        failed = db.scalars(select(PushSubscription).where(PushSubscription.family_id == family_id, PushSubscription.actor_role == "elder",
            PushSubscription.member_id == member_id,
            PushSubscription.last_error.in_(["DEVICE_UNREACHABLE", "PUSH_NOT_CONFIGURED"]))).all()
        return {"items": [{"delivery_id": d.id, "title": r.title, "triggered_at": t.triggered_at.isoformat(), "status": d.status} for r, t, d in rows],
            "device_alerts": [{"label": "长辈设备", "status": "推送服务未配置" if row.last_error == "PUSH_NOT_CONFIGURED" else "可能离线"} for row in failed]}
