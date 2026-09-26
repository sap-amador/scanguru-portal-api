"""Public signup endpoint: POST /api/v1/signup

Accepts the marketing-site form submission, stores a durable pending request,
notifies the approval inbox(es), and audits. Deliberately unauthenticated and
rate-limited. Email is best-effort — a mail outage never fails the request, so
the applicant always gets a clean confirmation and the row is always saved.
"""
import re
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, ConfigDict
from sqlalchemy.orm import Session

from app.audit import audit
from app.config import settings
from app.database import get_db
from app.mailer import send_email
from app.ratelimit import limiter
from app.signup_models import SignupRequest
from app.signup_admin import (verify_review_token, review_link, request_to_dict,
                              approve_request, reject_request)

router = APIRouter()


class SignupIn(BaseModel):
    # Accept exactly what signup.html posts; ignore extras (consent, submitted_at).
    model_config = ConfigDict(extra="ignore")
    first: str
    last: str
    email: EmailStr
    org: str
    role: str
    country: str
    interest: str | None = None
    msg: str | None = None
    source: str | None = None
    # SIGNUP_ANTISPAM_V1 — honeypot (must stay empty) and page-load epoch ms
    website: str | None = None
    form_ts: int | None = None


_URL_RE = re.compile(r"(https?://|www\.|bit\.ly|t\.co/|tinyurl|\.ru\b|\.xyz\b)", re.I)
_MIN_FILL_SECONDS = 4


_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2700-\u27BF\u2600-\u26FF]")
_NON_LATIN_SCRIPT_RE = re.compile(r"[\u0400-\u04FF\u0600-\u06FF\u4E00-\u9FFF\u0370-\u03FF\u0900-\u0DFF]")


def spam_reasons(body: "SignupIn") -> list[str]:
    """HARD signals quarantine on their own. SOFT signals need two to stack —
    a real doctor may have a digit in a handle-like name, type their name in
    Greek or Tamil, or be a fast typist; none of those alone is spam."""
    hard: list[str] = []
    soft: list[str] = []
    name = f"{body.first} {body.last}"
    if (body.website or "").strip():
        hard.append("HONEYPOT_FILLED")
    if _URL_RE.search(name) or _URL_RE.search(body.org or ""):
        hard.append("URL_IN_NAME_OR_ORG")
    if _URL_RE.search(body.msg or ""):
        hard.append("URL_IN_MESSAGE")
    if _EMOJI_RE.search(name):
        hard.append("EMOJI_IN_NAME")
    if body.msg and body.msg.strip() and body.msg.strip() == name.strip():
        hard.append("MESSAGE_EQUALS_NAME")

    if len(body.first.strip()) > 40 or len(body.last.strip()) > 40:
        soft.append("NAME_TOO_LONG")
    if re.search(r"\d", name):
        soft.append("DIGITS_IN_NAME")
    if _NON_LATIN_SCRIPT_RE.search(name):
        soft.append("NON_LATIN_SCRIPT_IN_NAME")
    if body.form_ts:
        elapsed = time.time() - body.form_ts / 1000.0
        if elapsed < _MIN_FILL_SECONDS:
            soft.append(f"FILLED_IN_{elapsed:.1f}S")

    if hard:
        return hard + soft
    if len(soft) >= 2:
        return soft
    return []


class SignupAck(BaseModel):
    ok: bool = True


@router.post("", response_model=SignupAck)
@limiter.limit("5/hour")   # per-IP: light spam guard, generous for real use
def create_signup(
    body: SignupIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    ip = request.client.host if request.client else None
    reasons = spam_reasons(body)
    req = SignupRequest(
        first_name=body.first.strip(),
        last_name=body.last.strip(),
        email=body.email.lower().strip(),
        org_name=body.org.strip(),
        role_text=body.role.strip(),
        country=body.country.strip(),
        interest=(body.interest or None),
        message=(body.msg or None),
        source=(body.source or "scanguru.ai/signup"),
        ip_address=ip,
    )
    if reasons:
        req.status = "spam"
        req.review_note = "AUTO_SPAM: " + " ".join(reasons)
    db.add(req)
    db.commit()
    db.refresh(req)

    if reasons:
        audit(db, None, "signup.spam", "signup_request", req.id, ip=ip, success=False,
              extra={"email": req.email, "reasons": reasons})
        return SignupAck()   # quarantined: no notification, bot sees a normal ack

    audit(db, None, "signup.request", "signup_request", req.id, ip=ip,
          extra={"email": req.email, "org": req.org_name, "role": req.role_text,
                 "country": req.country})

    # Best-effort admin/sales notification — never blocks the response.
    notify = [e for e in settings.signup_notify_emails.split(",") if e.strip()]
    if notify:
        link = review_link(req.id)
        text = (
            "NEW ACCESS REQUEST  —  passed automatic spam checks\n"
            "====================================================\n\n"
            f"  Who      : {req.first_name} {req.last_name}  ({req.role_text})\n"
            f"  Email    : {req.email}\n"
            f"  Where    : {req.org_name}, {req.country}\n"
            f"  Interest : {req.interest or '-'}\n\n"
            f"  Message  :\n  {(req.message or '(none)').strip().replace(chr(10), chr(10) + '  ')}\n\n"
            "----------------------------------------------------\n"
            "REVIEW, APPROVE OR REJECT (one click, link valid 30 days):\n\n"
            f"  {link}\n\n"
            "Approving creates the account and emails the applicant a temporary password.\n"
            "Rejecting sends a courteous decline. Either way you can add a personal note.\n\n"
            f"Fallback (terminal):  railway ssh  →  python approve_signup.py --approve {req.id}\n"
            f"Request ID: {req.id} · from {req.source} · IP {req.ip_address or '-'}\n"
        )
        send_email(notify, f"ScanGuru access request — {req.org_name}", text, reply_to=req.email)

    return SignupAck(ok=True)


# ---------------------------------------------------------------------------
# SIGNUP_REVIEW_V1 — one-click review from the notification email.
# Authenticated by the signed token in the link, not by a portal login.
# ---------------------------------------------------------------------------
class ReviewAction(BaseModel):
    t: str
    note: str | None = None


def _load_for_review(db: Session, request_id: uuid.UUID, token: str) -> SignupRequest:
    if not verify_review_token(str(request_id), token):
        raise HTTPException(403, "This review link is invalid or has expired.")
    r = db.get(SignupRequest, request_id)
    if not r:
        raise HTTPException(404, "Request not found.")
    return r


@router.get("/review/{request_id}")
@limiter.limit("60/hour")
def review_get(request_id: uuid.UUID, t: str, request: Request,
               db: Annotated[Session, Depends(get_db)]):
    return request_to_dict(_load_for_review(db, request_id, t))


@router.post("/review/{request_id}/approve")
@limiter.limit("30/hour")
def review_approve(request_id: uuid.UUID, body: ReviewAction, request: Request,
                   db: Annotated[Session, Depends(get_db)]):
    r = _load_for_review(db, request_id, body.t)
    result = approve_request(db, r, note=body.note)
    ip = request.client.host if request.client else None
    audit(db, None, "signup.approved" if result.get("ok") else "signup.approve_noop",
          "signup_request", r.id, ip=ip, extra={"email": r.email, "via": "review_link",
          "email_sent": result.get("email_sent"), "note": body.note})
    return result


@router.post("/review/{request_id}/reject")
@limiter.limit("30/hour")
def review_reject(request_id: uuid.UUID, body: ReviewAction, request: Request,
                  db: Annotated[Session, Depends(get_db)]):
    r = _load_for_review(db, request_id, body.t)
    result = reject_request(db, r, note=body.note)
    ip = request.client.host if request.client else None
    audit(db, None, "signup.rejected" if result.get("ok") else "signup.reject_noop",
          "signup_request", r.id, ip=ip, extra={"email": r.email, "via": "review_link", "note": body.note})
    return result
