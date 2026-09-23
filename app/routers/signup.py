"""Public signup endpoint: POST /api/v1/signup

Accepts the marketing-site form submission, stores a durable pending request,
notifies the approval inbox(es), and audits. Deliberately unauthenticated and
rate-limited. Email is best-effort — a mail outage never fails the request, so
the applicant always gets a clean confirmation and the row is always saved.
"""
import re
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, ConfigDict
from sqlalchemy.orm import Session

from app.audit import audit
from app.config import settings
from app.database import get_db
from app.mailer import send_email
from app.ratelimit import limiter
from app.signup_models import SignupRequest

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
_NON_LATIN_RE = re.compile(r"[\u0400-\u04FF\u0600-\u06FF\u4E00-\u9FFF\U0001F300-\U0001FAFF\u2700-\u27BF]")
_MIN_FILL_SECONDS = 4


def spam_reasons(body: "SignupIn") -> list[str]:
    """Cheap heuristics. Any hit => quarantine (status='spam'), never email."""
    r: list[str] = []
    if (body.website or "").strip():
        r.append("HONEYPOT_FILLED")
    name = f"{body.first} {body.last}"
    if _URL_RE.search(name) or _URL_RE.search(body.org or ""):
        r.append("URL_IN_NAME_OR_ORG")
    if _URL_RE.search(body.msg or ""):
        r.append("URL_IN_MESSAGE")
    if len(body.first.strip()) > 40 or len(body.last.strip()) > 40:
        r.append("NAME_TOO_LONG")
    if re.search(r"\d", name):
        r.append("DIGITS_IN_NAME")
    if _NON_LATIN_RE.search(name):
        r.append("NON_LATIN_OR_EMOJI_IN_NAME")
    if body.msg and body.msg.strip() and body.msg.strip() == name.strip():
        r.append("MESSAGE_EQUALS_NAME")
    if body.form_ts:
        elapsed = time.time() - body.form_ts / 1000.0
        if elapsed < _MIN_FILL_SECONDS:
            r.append(f"FILLED_IN_{elapsed:.1f}S")
    return r


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
        text = (
            "New ScanGuru access request\n"
            "===========================\n"
            f"Name        : {req.first_name} {req.last_name}\n"
            f"Email       : {req.email}\n"
            f"Organization: {req.org_name}\n"
            f"Role        : {req.role_text}\n"
            f"Country     : {req.country}\n"
            f"Interest    : {req.interest or '-'}\n"
            f"Source      : {req.source}\n"
            f"Request ID  : {req.id}\n\n"
            f"Message:\n{req.message or '(none)'}\n\n"
            f"Approve with:  python approve_signup.py --approve {req.id}\n"
        )
        send_email(notify, f"ScanGuru access request — {req.org_name}", text, reply_to=req.email)

    return SignupAck(ok=True)
