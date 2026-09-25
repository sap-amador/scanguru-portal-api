"""Signup review actions shared by the web review page and approve_signup.py.

SIGNUP_REVIEW_V1 — the notification email carries a link to
https://scanguru.ai/approve.html?id=<request>&t=<token>. The token is an
HMAC of the request id with the server secret, valid 30 days. Whoever holds
the admin inbox can approve or reject with one click; nobody else can.
"""
from __future__ import annotations
import hashlib, hmac, secrets, time
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import settings
from app.mailer import send_email
from app.models import Org, User, UserRole, AccessMode
from app.signup_models import SignupRequest

REVIEW_TOKEN_TTL_SECONDS = 30 * 24 * 3600
REVIEW_PAGE_URL = getattr(settings, "signup_review_page_url", "https://scanguru.ai/approve.html")

_ROLE_MAP = {
    "radiologist": "radiologist", "clinician / physician": "radiologist", "clinician": "radiologist",
    "physician": "radiologist", "researcher": "radiologist", "student / trainee": "radiologist",
    "it / administration": "technologist", "other": "radiologist",
}


def map_role(role_text: str):
    return getattr(UserRole, _ROLE_MAP.get((role_text or "").strip().lower(), "radiologist"))


# --- signed review link -----------------------------------------------------
def _sig(request_id: str, exp: int) -> str:
    msg = f"signup-review:{request_id}:{exp}".encode()
    return hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).hexdigest()[:40]


def make_review_token(request_id: str) -> str:
    exp = int(time.time()) + REVIEW_TOKEN_TTL_SECONDS
    return f"{exp}.{_sig(request_id, exp)}"


def verify_review_token(request_id: str, token: str) -> bool:
    try:
        exp_s, sig = (token or "").split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(_sig(request_id, exp), sig)


def review_link(request_id) -> str:
    rid = str(request_id)
    return f"{REVIEW_PAGE_URL}?id={rid}&t={make_review_token(rid)}"


# --- serialisation ------------------------------------------------------------
def request_to_dict(r: SignupRequest) -> dict:
    note = r.review_note or ""
    return {
        "id": str(r.id), "first_name": r.first_name, "last_name": r.last_name, "email": r.email,
        "org_name": r.org_name, "role_text": r.role_text, "country": r.country,
        "interest": r.interest, "message": r.message, "source": r.source, "ip_address": r.ip_address,
        "status": r.status, "review_note": r.review_note,
        "spam_flags": note.replace("AUTO_SPAM:", "").split() if note.startswith("AUTO_SPAM") else [],
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
    }


# --- actions -------------------------------------------------------------------
def _welcome_body(r: SignupRequest, org_name: str, temp_pw: str, note: Optional[str]) -> str:
    personal = f"A note from the ScanGuru team:\n  {note.strip()}\n\n" if note and note.strip() else ""
    return (
        f"Hello {r.first_name},\n\n"
        f"Good news: your ScanGuru access request for {org_name} has been approved.\n\n"
        f"{personal}"
        "SIGN IN\n"
        f"  Portal   : {settings.portal_login_url}\n"
        f"  Email    : {r.email}\n"
        f"  Password : {temp_pw}   (temporary — you will be asked to set your own on first sign-in)\n\n"
        "WHAT YOU CAN DO\n"
        "  Upload a chest X-ray, CT, MRI, mammogram, dental or MSK image (DICOM, PNG, JPG,\n"
        "  or a .zip of a DICOM series) and receive a clinical, research and patient report\n"
        "  in English plus your chosen language. CT and MRI series take 2-4 minutes.\n\n"
        "PLEASE NOTE\n"
        "  ScanGuru is provided for research and evaluation. It is built to support a\n"
        "  qualified clinician's judgment, not to replace it, and is not a cleared medical device.\n\n"
        "Questions or trouble signing in: reply to this email or write to support@scanguru.ai.\n\n"
        "Warm regards,\nThe ScanGuru team\nhttps://scanguru.ai\n"
    )


def _reject_body(r: SignupRequest, note: Optional[str]) -> str:
    reason = (note.strip() if note and note.strip() else
              "For this phase of the pilot we are prioritising clinical and academic partners, "
              "and we are not able to open an account for you right now. We will keep your details "
              "and let you know when the next wave opens.")
    return (
        f"Hello {r.first_name},\n\n"
        "Thank you for your interest in ScanGuru.\n\n"
        f"{reason}\n\n"
        "If you believe this was a mistake, or your situation changes, just reply to this email.\n\n"
        "Warm regards,\nThe ScanGuru team\nhttps://scanguru.ai\n"
    )


def approve_request(db: Session, r: SignupRequest, note: Optional[str] = None,
                    role_override: Optional[str] = None) -> dict:
    if r.status != "pending":
        return {"ok": False, "error": f"Request is already {r.status}", "status": r.status}

    existing = db.query(User).filter(User.email == r.email).first()
    if existing:
        r.status = "approved"; r.created_user_id = existing.id
        r.review_note = note; r.reviewed_at = func.now()
        db.commit()
        return {"ok": True, "action": "approved", "email": r.email, "reused_user": True,
                "org": None, "temp_password": None, "email_sent": False,
                "message": "A portal user with this email already exists; request marked approved, no new account."}

    org = db.query(Org).filter(Org.name == r.org_name).first()
    created_org = org is None
    if org is None:
        org = Org(name=r.org_name, region="us", access_mode=AccessMode.shared)
        db.add(org); db.flush()

    role = getattr(UserRole, role_override) if role_override else map_role(r.role_text)
    temp_pw = secrets.token_urlsafe(9)
    user = User(org_id=org.id, email=r.email, password_hash=hash_password(temp_pw),
                full_name=f"{r.first_name} {r.last_name}".strip(),
                role=role, is_active=True, totp_enabled=False)
    db.add(user); db.flush()

    r.status = "approved"; r.created_user_id = user.id
    r.review_note = note; r.reviewed_at = func.now()
    db.commit()

    sent = send_email(r.email, "Your ScanGuru account is ready — sign in details",
                      _welcome_body(r, org.name, temp_pw, note))
    return {"ok": True, "action": "approved", "email": r.email, "reused_user": False,
            "org": org.name, "created_org": created_org, "role": role.value,
            "temp_password": temp_pw, "email_sent": bool(sent)}


def reject_request(db: Session, r: SignupRequest, note: Optional[str] = None) -> dict:
    if r.status != "pending":
        return {"ok": False, "error": f"Request is already {r.status}", "status": r.status}
    r.status = "rejected"; r.review_note = note; r.reviewed_at = func.now()
    db.commit()
    sent = send_email(r.email, "Your ScanGuru access request", _reject_body(r, note))
    return {"ok": True, "action": "rejected", "email": r.email, "email_sent": bool(sent)}
