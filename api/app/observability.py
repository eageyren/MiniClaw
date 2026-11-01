"""Local structured request tracing with optional production persistence."""
import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from app.config import settings
from app.database import SessionLocal
from app.models import RequestTrace, TraceSpan

logger = logging.getLogger("miniclaw.trace")
_REQUEST_ID: ContextVar[str | None] = ContextVar("miniclaw_request_id", default=None)

def current_request_id() -> str | None:
    return _REQUEST_ID.get()

def set_request_id(value: str):
    return _REQUEST_ID.set(value)

def reset_request_id(token):
    _REQUEST_ID.reset(token)

def trace_start(request_id: str, route: str, identity=None) -> None:
    now = datetime.now(timezone.utc)
    family_id = identity.family_id if identity else None
    with SessionLocal() as db:
        db.add(RequestTrace(id=request_id, family_id=family_id, member_id=identity.member_id if identity else None,
            actor_role=identity.role if identity else None, route=route[:200], started_at=now))
        db.commit()
    if settings.app_env != "production":
        logger.info(json.dumps({"request_id": request_id, "event": "request_started", "family_id": family_id,
            "member_id": identity.member_id if identity else None, "role": identity.role if identity else None, "route": route}, ensure_ascii=False))

def trace_finish(request_id: str, status_code: int, duration_ms: int) -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.get(RequestTrace, request_id)
        if row:
            row.status_code = status_code; row.finished_at = now; row.duration_ms = max(0, duration_ms); db.commit()
    if settings.app_env != "production":
        logger.info(json.dumps({"request_id": request_id, "event": "request_finished", "status_code": status_code, "duration_ms": max(0, duration_ms)}, ensure_ascii=False))

def trace_event(span_name: str, duration_ms: int = 0, outcome: str = "ok", metadata: dict | None = None,
                request_id: str | None = None, family_id: str | None = None) -> None:
    trace_id = request_id or current_request_id()
    if not trace_id: return
    if family_id is None:
        try:
            from app.auth import request_identity
            family_id = request_identity().family_id
        except Exception:
            family_id = None
    identity = None
    try:
        from app.auth import request_identity
        identity = request_identity()
    except Exception:
        pass
    safe_metadata = metadata or {}
    with SessionLocal() as db:
        db.add(TraceSpan(id=str(uuid.uuid4()), request_id=trace_id, family_id=family_id,
            span_name=span_name[:80], duration_ms=max(0, int(duration_ms)), outcome=outcome[:40], metadata_json=safe_metadata))
        db.commit()
    if settings.app_env != "production":
        logger.info(json.dumps({"request_id": trace_id, "event": span_name, "duration_ms": max(0, int(duration_ms)),
            "outcome": outcome, "family_id": family_id, "member_id": identity.member_id if identity else None,
            "role": identity.role if identity else None, **safe_metadata}, ensure_ascii=False, default=str))
