"""PWRESET_V1 — self-service password reset.
POST /auth/forgot-password {email}            -> always 200; emails a signed link if the user exists
POST /auth/reset-password  {email, token, new_password}
Token = HMAC(secret, user id + current hash prefix + expiry): single-use (changes with the hash), 1 h."""
import hashlib, hmac, time
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from app.audit import audit
from app.auth import hash_password
from app.config import settings
from app.database import get_db
from app.mailer import send_email
from app.models import User
from app.ratelimit import limiter

router = APIRouter()
TTL = 3600
RESET_PAGE = settings.portal_login_url.rsplit("/", 1)[0] + "/portal-reset.html"


def _sig(u: User, exp: int) -> str:
    msg = f"pwreset:{u.id}:{u.password_hash[:16]}:{exp}".encode()
    return hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).hexdigest()[:40]


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    email: EmailStr
    token: str
    new_password: str


@router.post("/forgot-password")
@limiter.limit("5/hour")
def forgot_password(body: ForgotIn, request: Request, db: Annotated[Session, Depends(get_db)]):
    ip = request.client.host if request.client else None
    u = db.query(User).filter(User.email == body.email.lower()).first()
    if u and u.is_active:
        exp = int(time.time()) + TTL
        link = f"{RESET_PAGE}?e={u.email}&t={exp}.{_sig(u, exp)}"
        send_email(u.email, "Reset your ScanGuru password",
                   f"Hello {u.full_name.split(' ')[0] if u.full_name else ''},\n\n"
                   "Someone (hopefully you) asked to reset the password for this ScanGuru account.\n\n"
                   f"Set a new password here (link valid for 1 hour):\n  {link}\n\n"
                   "If you did not request this, you can ignore this email — your password is unchanged.\n\n"
                   "The ScanGuru team\n")
        audit(db, u, "auth.password_reset.requested", "user", u.id, ip=ip)
    else:
        audit(db, None, "auth.password_reset.unknown_email", "user", None, success=False, ip=ip, extra={"email": body.email})
    return {"ok": True, "message": "If that email belongs to an active account, a reset link has been sent."}


@router.post("/reset-password")
@limiter.limit("10/hour")
def reset_password(body: ResetIn, request: Request, db: Annotated[Session, Depends(get_db)]):
    ip = request.client.host if request.client else None
    u = db.query(User).filter(User.email == body.email.lower()).first()
    bad = HTTPException(400, "This reset link is invalid or has expired. Request a new one.")
    if not u or not u.is_active:
        raise bad
    try:
        exp_s, sig = body.token.split(".", 1); exp = int(exp_s)
    except ValueError:
        raise bad
    if exp < time.time() or not hmac.compare_digest(_sig(u, exp), sig):
        raise bad
    if len(body.new_password) < 10:
        raise HTTPException(400, "Password must be at least 10 characters.")
    u.password_hash = hash_password(body.new_password)
    db.commit()
    audit(db, u, "auth.password_reset.completed", "user", u.id, ip=ip)
    return {"ok": True}
