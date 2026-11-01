"""Opaque bearer tokens, one-time pairing, family OTP login and request identity."""
import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import urllib.request
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import AccessSession, DeviceCredential, FamilyAccess, FamilyAccount, Member, OtpCode, PairingCode

logger = logging.getLogger(__name__)
ACCESS_SECONDS = 15 * 60
REFRESH_DAYS = 365
PAIRING_SECONDS = 10 * 60
OTP_SECONDS = 5 * 60

@dataclass(frozen=True)
class Identity:
    family_id: str
    member_id: str
    role: str
    actor_id: str
    display_name: str
    access_session_id: str
    family_account_id: str | None = None
    device_id: str | None = None

_IDENTITY: ContextVar[Identity | None] = ContextVar("miniclaw_identity", default=None)

def set_identity(identity: Identity): return _IDENTITY.set(identity)
def reset_identity(token): _IDENTITY.reset(token)

def request_identity() -> Identity:
    identity = _IDENTITY.get()
    if identity is not None: return identity
    # Background workers and migration/bootstrap routines use this configured tenant.
    return Identity(settings.family_id, settings.member_id, "elder", settings.member_id, "长辈", "background")

def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def normalize_phone(value: str) -> str:
    return value.strip().replace(" ", "")

def issue_access(db, family_id: str, member_id: str, role: str, actor_id: str, display_name: str, family_account_id: str | None = None, device_id: str | None = None) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(seconds=ACCESS_SECONDS)
    db.add(AccessSession(id=str(uuid.uuid4()), token_hash=digest(token), role=role, family_id=family_id, member_id=member_id,
        family_account_id=family_account_id, device_id=device_id, expires_at=expires))
    return token, expires

def issue_refresh_secret() -> str:
    return secrets.token_urlsafe(48)

def create_pairing() -> dict:
    now = datetime.now(timezone.utc)
    secret = issue_refresh_secret()
    with SessionLocal() as db:
        for _ in range(8):
            code = f"{secrets.randbelow(1_000_000):06d}"
            existing = db.scalar(select(PairingCode.id).where(PairingCode.code_hash == digest(code), PairingCode.expires_at > now))
            if not existing: break
        row = PairingCode(id=str(uuid.uuid4()), code_hash=digest(code), enrollment_secret_hash=digest(secret), family_id=settings.family_id,
            member_id=settings.member_id, expires_at=now + timedelta(seconds=PAIRING_SECONDS))
        db.add(row); db.commit()
    return {"pairing_code": code, "enrollment_secret": secret, "expires_at": row.expires_at.isoformat()}

async def send_otp(phone_number: str) -> str | None:
    phone_number = normalize_phone(phone_number)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        recent = db.scalar(select(OtpCode).where(OtpCode.phone_number == phone_number, OtpCode.created_at >= now - timedelta(seconds=60)).order_by(OtpCode.created_at.desc()).limit(1))
        if recent: raise ValueError("验证码刚发送过，请稍等一分钟再试。")
        code = f"{secrets.randbelow(1_000_000):06d}"
        db.add(OtpCode(id=str(uuid.uuid4()), phone_number=phone_number, code_hash=digest(code), expires_at=now + timedelta(seconds=OTP_SECONDS)))
        db.commit()
    if settings.app_env == "development" and not settings.sms_gateway_url:
        logger.info("Development OTP generated")
        return code
    if not settings.sms_gateway_url or not settings.sms_gateway_token:
        raise RuntimeError("短信服务尚未配置，暂时无法发送验证码。")
    payload = json.dumps({"phone_number": phone_number, "message": f"MiniClaw 验证码：{code}，5 分钟内有效。"}).encode("utf-8")
    def send():
        request = urllib.request.Request(settings.sms_gateway_url, data=payload, headers={"Authorization": f"Bearer {settings.sms_gateway_token}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=8) as response:
            if response.status >= 300: raise RuntimeError("短信发送失败")
    await asyncio.to_thread(send)
    return None

def consume_otp(db, phone_number: str, code: str) -> bool:
    now = datetime.now(timezone.utc)
    row = db.scalar(select(OtpCode).where(OtpCode.phone_number == normalize_phone(phone_number), OtpCode.consumed_at.is_(None), OtpCode.expires_at > now).order_by(OtpCode.created_at.desc()).limit(1).with_for_update())
    if not row or row.attempts >= 5: return False
    if not hmac.compare_digest(row.code_hash, digest(code)):
        row.attempts += 1
        db.commit()
        return False
    row.consumed_at = now
    db.commit()
    return True

def pair_family(phone_number: str, display_name: str, pairing_code: str, otp: str) -> tuple[str, str, datetime]:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        if not consume_otp(db, phone_number, otp): raise ValueError("验证码不正确或已过期，请重新获取。")
        pairing = db.scalar(select(PairingCode).where(PairingCode.code_hash == digest(pairing_code), PairingCode.expires_at > now, PairingCode.paired_at.is_(None)).with_for_update())
        if not pairing: raise ValueError("这个配对码过期了，或已经使用过。请让长辈重新生成一个。")
        phone = normalize_phone(phone_number)
        account = db.scalar(select(FamilyAccount).where(FamilyAccount.phone_number == phone).with_for_update())
        if account and account.family_id != pairing.family_id: raise ValueError("这个手机号已经绑定了另一个家庭。")
        if account and db.scalar(select(FamilyAccess.id).where(FamilyAccess.family_account_id == account.id, FamilyAccess.revoked_at.is_(None))):
            raise ValueError("这个手机号已经加入该家庭，请改用家属登录。")
        if not account:
            account = FamilyAccount(id=str(uuid.uuid4()), family_id=pairing.family_id, phone_number=phone, display_name=display_name.strip())
            db.add(account); db.flush()
        link = FamilyAccess(id=str(uuid.uuid4()), family_id=pairing.family_id, member_id=pairing.member_id, family_account_id=account.id)
        db.add(link); pairing.paired_at = now
        access, expires = issue_access(db, pairing.family_id, pairing.member_id, "family", account.id, account.display_name, family_account_id=account.id)
        refresh = issue_refresh_secret()
        # A family refresh credential is stored as an access-session with a long expiry and separately identified by its role.
        refresh_expiry = now + timedelta(days=REFRESH_DAYS)
        db.add(AccessSession(id=str(uuid.uuid4()), token_hash=digest(refresh), role="family_refresh", family_id=pairing.family_id, member_id=pairing.member_id, family_account_id=account.id, expires_at=refresh_expiry))
        db.commit()
        return access, refresh, expires

def login_family(phone_number: str, otp: str) -> tuple[str, str, datetime]:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        if not consume_otp(db, phone_number, otp): raise ValueError("验证码不正确或已过期，请重新获取。")
        account = db.scalar(select(FamilyAccount).join(FamilyAccess, FamilyAccess.family_account_id == FamilyAccount.id).where(FamilyAccount.phone_number == normalize_phone(phone_number), FamilyAccess.revoked_at.is_(None)))
        if not account: raise LookupError("这个手机号还没有绑定家庭，请先输入长辈设备上的配对码。")
        link = db.scalar(select(FamilyAccess).where(FamilyAccess.family_account_id == account.id, FamilyAccess.revoked_at.is_(None)))
        access, expires = issue_access(db, account.family_id, link.member_id, "family", account.id, account.display_name, family_account_id=account.id)
        refresh = issue_refresh_secret()
        db.add(AccessSession(id=str(uuid.uuid4()), token_hash=digest(refresh), role="family_refresh", family_id=account.family_id, member_id=link.member_id, family_account_id=account.id, expires_at=now + timedelta(days=REFRESH_DAYS)))
        db.commit()
        return access, refresh, expires

def poll_pairing(enrollment_secret: str, device_label: str = "长辈设备") -> dict:
    now = datetime.now(timezone.utc)
    secret_hash = digest(enrollment_secret)
    with SessionLocal() as db:
        pairing = db.scalar(select(PairingCode).where(PairingCode.enrollment_secret_hash == secret_hash).order_by(PairingCode.created_at.desc()).limit(1))
        if not pairing or (pairing.paired_at is None and pairing.expires_at <= now): raise LookupError("这个码过期了，我再说一个新的。")
        if pairing.paired_at is None: return {"status": "pending"}
        device = db.scalar(select(DeviceCredential).where(DeviceCredential.pairing_code_id == pairing.id))
        if not device:
            device = DeviceCredential(id=str(uuid.uuid4()), family_id=pairing.family_id, member_id=pairing.member_id, pairing_code_id=pairing.id,
                refresh_hash=secret_hash, device_label=device_label[:120], expires_at=now + timedelta(days=REFRESH_DAYS))
            db.add(device); db.flush()
        access, expires = issue_access(db, device.family_id, device.member_id, "elder", device.member_id, "长辈", device_id=device.id)
        db.commit()
        return {"status": "paired", "access_token": access, "refresh_token": enrollment_secret, "expires_at": expires.isoformat()}

def refresh_access(refresh_token: str) -> dict:
    now = datetime.now(timezone.utc)
    token_hash = digest(refresh_token)
    with SessionLocal() as db:
        device = db.scalar(select(DeviceCredential).where(DeviceCredential.refresh_hash == token_hash, DeviceCredential.revoked_at.is_(None), DeviceCredential.expires_at > now))
        if device:
            access, expires = issue_access(db, device.family_id, device.member_id, "elder", device.member_id, "长辈", device_id=device.id)
        else:
            refresh = db.scalar(select(AccessSession).where(AccessSession.token_hash == token_hash, AccessSession.role == "family_refresh", AccessSession.revoked_at.is_(None), AccessSession.expires_at > now))
            if not refresh: raise LookupError("令牌已经失效，请重新配对或登录。")
            link = db.scalar(select(FamilyAccess).where(FamilyAccess.family_account_id == refresh.family_account_id, FamilyAccess.revoked_at.is_(None)))
            account = db.get(FamilyAccount, refresh.family_account_id)
            if not link or not account: raise LookupError("家庭授权已撤销，请重新联系长辈授权。")
            access, expires = issue_access(db, refresh.family_id, refresh.member_id, "family", account.id, account.display_name, family_account_id=account.id)
        db.commit()
        return {"access_token": access, "expires_at": expires.isoformat()}

def authenticate_access(token: str) -> Identity | None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        session = db.scalar(select(AccessSession).where(AccessSession.token_hash == digest(token), AccessSession.role.in_(["elder", "family"]), AccessSession.revoked_at.is_(None), AccessSession.expires_at > now))
        if not session: return None
        if session.role == "family":
            link = db.scalar(select(FamilyAccess).where(FamilyAccess.family_account_id == session.family_account_id, FamilyAccess.revoked_at.is_(None)))
            account = db.get(FamilyAccount, session.family_account_id)
            if not link or not account: return None
            return Identity(session.family_id, session.member_id, "family", account.id, account.display_name, session.id, account.id)
        device = db.get(DeviceCredential, session.device_id) if session.device_id else None
        if not device or device.revoked_at or device.expires_at <= now: return None
        member = db.get(Member, session.member_id)
        return Identity(session.family_id, session.member_id, "elder", session.member_id, member.display_name if member else "长辈", session.id, device_id=device.id)

def require_role(role: str) -> Identity:
    identity = request_identity()
    if identity.role != role:
        from fastapi import HTTPException
        raise HTTPException(404, "找不到这个页面")
    return identity

async def authenticate_request(request: Request) -> Identity | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "): return None
    return await asyncio.to_thread(authenticate_access, authorization[7:].strip())
