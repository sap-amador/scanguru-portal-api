"""Public signup endpoint: POST /api/v1/signup

Accepts the marketing-site form submission, stores a durable pending request,
notifies the approval inbox(es), and audits. Deliberately unauthenticated and
rate-limited. Email is best-effort — a mail outage never fails the request, so
the applicant always gets a clean confirmation and the row is always saved.
"""
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
    db.add(req)
    db.commit()
    db.refresh(req)

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
