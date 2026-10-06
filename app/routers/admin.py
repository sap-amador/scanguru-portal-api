"""ADMIN_V1 — super-admin console API. Guarded by require_superadmin
(email allow-list in SUPERADMIN_EMAILS). Every mutation is audited."""
import secrets, uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select, desc, or_
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import require_superadmin, hash_password
from app.config import settings
from app.database import get_db
from app.mailer import send_email
from app.models import Org, User, UserRole, AccessMode, Study, Report, Patient, AuditLog, UsageCounter
from app.signup_models import SignupRequest
from app.signup_admin import approve_request, reject_request

router = APIRouter()
SA = Annotated[User, Depends(require_superadmin)]
DB = Annotated[Session, Depends(get_db)]


def _ip(request: Request):
    return request.client.host if request.client else None


# --- overview ------------------------------------------------------------------
@router.get("/overview")
def overview(current: SA, db: DB):
    now = datetime.now(timezone.utc)
    d30 = now - timedelta(days=30)
    q = lambda stmt: db.execute(stmt).scalar() or 0
    weeks = []
    for i in range(11, -1, -1):
        start = now - timedelta(days=7 * (i + 1)); end = now - timedelta(days=7 * i)
        n = q(select(func.count(Study.id)).where(Study.created_at >= start, Study.created_at < end))
        weeks.append({"week_start": start.date().isoformat(), "studies": n})
    return {
        "orgs_total": q(select(func.count(Org.id))),
        "orgs_active": q(select(func.count(Org.id)).where(Org.is_active == True)),  # noqa: E712
        "users_total": q(select(func.count(User.id))),
        "users_active": q(select(func.count(User.id)).where(User.is_active == True)),  # noqa: E712
        "studies_total": q(select(func.count(Study.id))),
        "studies_30d": q(select(func.count(Study.id)).where(Study.created_at >= d30)),
        "signups_pending": q(select(func.count(SignupRequest.id)).where(SignupRequest.status == "pending")),
        "signups_spam": q(select(func.count(SignupRequest.id)).where(SignupRequest.status == "spam")),
        "logins_30d": q(select(func.count(AuditLog.id)).where(AuditLog.action == "auth.login.success", AuditLog.occurred_at >= d30)),
        "studies_by_week": weeks,
        "superadmin": current.email,
    }


# --- organisations ---------------------------------------------------------------
class OrgPatch(BaseModel):
    is_active: Optional[bool] = None
    status_note: Optional[str] = None
    name: Optional[str] = None
    region: Optional[str] = None
    access_mode: Optional[AccessMode] = None
    free_tier_verified: Optional[bool] = None


@router.get("/orgs")
def list_orgs(current: SA, db: DB):
    now = datetime.now(timezone.utc); d30 = now - timedelta(days=30)
    users_c = select(User.org_id, func.count(User.id).label("n"), func.sum(func.cast(User.is_active, type_=None)).label("_")).group_by(User.org_id).subquery() if False else None
    rows = db.execute(select(Org).order_by(Org.name)).scalars().all()
    out = []
    for o in rows:
        uc = db.execute(select(func.count(User.id)).where(User.org_id == o.id)).scalar() or 0
        ua = db.execute(select(func.count(User.id)).where(User.org_id == o.id, User.is_active == True)).scalar() or 0  # noqa: E712
        st = db.execute(select(func.count(Study.id)).where(Study.org_id == o.id)).scalar() or 0
        s30 = db.execute(select(func.count(Study.id)).where(Study.org_id == o.id, Study.created_at >= d30)).scalar() or 0
        last = db.execute(select(func.max(Study.created_at)).where(Study.org_id == o.id)).scalar()
        out.append({"id": str(o.id), "name": o.name, "region": o.region, "access_mode": o.access_mode.value,
                    "is_active": bool(getattr(o, "is_active", True)), "status_note": o.status_note,
                    "free_tier_type": o.free_tier_type, "free_tier_verified": o.free_tier_verified,
                    "users": uc, "users_active": ua, "studies_total": st, "studies_30d": s30,
                    "last_study_at": last.isoformat() if last else None})
    return out


@router.patch("/orgs/{org_id}")
def patch_org(org_id: uuid.UUID, body: OrgPatch, request: Request, current: SA, db: DB):
    o = db.get(Org, org_id)
    if not o:
        raise HTTPException(404, "Org not found")
    if body.is_active is False and o.id == current.org_id:
        raise HTTPException(400, "You cannot suspend your own organisation from inside it")
    changes = {}
    for f in ("is_active", "status_note", "name", "region", "access_mode", "free_tier_verified"):
        v = getattr(body, f)
        if v is not None and getattr(o, f) != v:
            changes[f] = v.value if hasattr(v, "value") else v
            setattr(o, f, v)
    db.commit()
    audit(db, current, "admin.org.update", "org", o.id, ip=_ip(request), extra=changes)
    return {"ok": True, "changes": changes}


# --- users -------------------------------------------------------------------------
class UserPatch(BaseModel):
    is_active: Optional[bool] = None
    blocked_note: Optional[str] = None
    role: Optional[UserRole] = None
    full_name: Optional[str] = None
    org_id: Optional[uuid.UUID] = None


@router.get("/users")
def list_users(current: SA, db: DB, org_id: Optional[uuid.UUID] = None, q: Optional[str] = None,
               limit: int = Query(200, le=1000)):
    stmt = select(User, Org.name).join(Org, Org.id == User.org_id)
    if org_id:
        stmt = stmt.where(User.org_id == org_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.full_name).like(like)))
    rows = db.execute(stmt.order_by(Org.name, User.email).limit(limit)).all()
    out = []
    for u, org_name in rows:
        st = db.execute(select(func.count(Study.id)).where(Study.ordered_by == u.id)).scalar() or 0
        out.append({"id": str(u.id), "email": u.email, "full_name": u.full_name, "role": u.role.value,
                    "org_id": str(u.org_id), "org_name": org_name, "is_active": u.is_active,
                    "blocked_note": u.blocked_note, "totp_enabled": u.totp_enabled,
                    "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                    "created_at": u.created_at.isoformat() if u.created_at else None, "studies": st})
    return out


@router.patch("/users/{user_id}")
def patch_user(user_id: uuid.UUID, body: UserPatch, request: Request, current: SA, db: DB):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    if body.is_active is False and u.id == current.id:
        raise HTTPException(400, "You cannot deactivate yourself")
    changes = {}
    for f in ("is_active", "blocked_note", "role", "full_name", "org_id"):
        v = getattr(body, f)
        if v is not None and getattr(u, f) != v:
            changes[f] = str(v.value if hasattr(v, "value") else v)
            setattr(u, f, v)
    if body.is_active is True and body.blocked_note is None:
        u.blocked_note = None
    db.commit()
    audit(db, current, "admin.user.update", "user", u.id, ip=_ip(request), extra=changes)
    return {"ok": True, "changes": changes}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: uuid.UUID, request: Request, current: SA, db: DB):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    temp = secrets.token_urlsafe(9)
    u.password_hash = hash_password(temp)
    db.commit()
    sent = send_email(u.email, "Your ScanGuru password has been reset",
                      f"Hello {u.full_name.split(' ')[0] if u.full_name else ''},\n\n"
                      f"A ScanGuru administrator has reset your password.\n\n"
                      f"  Portal   : {settings.portal_login_url}\n  Email    : {u.email}\n"
                      f"  Password : {temp}   (temporary — set your own after signing in)\n\n"
                      "If you did not expect this, contact support@scanguru.ai.\n\nThe ScanGuru team\n")
    audit(db, current, "admin.user.reset_password", "user", u.id, ip=_ip(request), extra={"email_sent": bool(sent)})
    return {"ok": True, "temp_password": temp, "email_sent": bool(sent)}


# --- signup requests ----------------------------------------------------------------
class NoteBody(BaseModel):
    note: Optional[str] = None


@router.get("/signups")
def list_signups(current: SA, db: DB, status_: str = Query("pending", alias="status"), limit: int = Query(200, le=1000)):
    stmt = select(SignupRequest)
    if status_ != "all":
        stmt = stmt.where(SignupRequest.status == status_)
    rows = db.execute(stmt.order_by(desc(SignupRequest.created_at)).limit(limit)).scalars().all()
    return [{"id": str(r.id), "first_name": r.first_name, "last_name": r.last_name, "email": r.email,
             "org_name": r.org_name, "role_text": r.role_text, "country": r.country, "interest": r.interest,
             "message": r.message, "status": r.status, "review_note": r.review_note,
             "created_at": r.created_at.isoformat() if r.created_at else None,
             "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None} for r in rows]


@router.post("/signups/{request_id}/approve")
def admin_approve(request_id: uuid.UUID, body: NoteBody, request: Request, current: SA, db: DB):
    r = db.get(SignupRequest, request_id)
    if not r:
        raise HTTPException(404, "Request not found")
    res = approve_request(db, r, note=body.note)
    audit(db, current, "signup.approved", "signup_request", r.id, ip=_ip(request), extra={"via": "admin_console", **{k: v for k, v in res.items() if k != "temp_password"}})
    return res


@router.post("/signups/{request_id}/reject")
def admin_reject(request_id: uuid.UUID, body: NoteBody, request: Request, current: SA, db: DB):
    r = db.get(SignupRequest, request_id)
    if not r:
        raise HTTPException(404, "Request not found")
    res = reject_request(db, r, note=body.note)
    audit(db, current, "signup.rejected", "signup_request", r.id, ip=_ip(request), extra={"via": "admin_console"})
    return res


# --- audit log ---------------------------------------------------------------------------
@router.get("/audit")
def list_audit(current: SA, db: DB, org_id: Optional[uuid.UUID] = None, user_id: Optional[uuid.UUID] = None,
               action: Optional[str] = None, limit: int = Query(300, le=1000)):
    stmt = select(AuditLog, User.email, Org.name).outerjoin(User, User.id == AuditLog.actor_user_id).outerjoin(Org, Org.id == AuditLog.org_id)
    if org_id: stmt = stmt.where(AuditLog.org_id == org_id)
    if user_id: stmt = stmt.where(AuditLog.actor_user_id == user_id)
    if action: stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    rows = db.execute(stmt.order_by(desc(AuditLog.occurred_at)).limit(limit)).all()
    return [{"id": str(a.id), "at": a.occurred_at.isoformat() if a.occurred_at else None, "action": a.action,
             "resource_type": a.resource_type, "resource_id": str(a.resource_id) if a.resource_id else None,
             "actor": email, "org": org_name, "success": a.success, "ip": a.ip_address, "extra": a.extra}
            for a, email, org_name in rows]


# --- usage -------------------------------------------------------------------------------
@router.get("/usage")
def usage(current: SA, db: DB):
    d90 = datetime.now(timezone.utc) - timedelta(days=90)
    by_mod = db.execute(select(Study.modality, func.count(Study.id)).where(Study.created_at >= d90).group_by(Study.modality)).all()
    by_status = db.execute(select(Study.status, func.count(Study.id)).group_by(Study.status)).all()
    months = db.execute(select(UsageCounter.period_month, Org.name, UsageCounter.scans_used)
                        .join(Org, Org.id == UsageCounter.org_id).order_by(UsageCounter.period_month)).all()
    return {"by_modality_90d": [{"modality": m.value, "n": n} for m, n in by_mod],
            "by_status": [{"status": s.value, "n": n} for s, n in by_status],
            "per_org_month": [{"month": pm.isoformat(), "org": name, "scans": sc} for pm, name, sc in months]}
