#!/usr/bin/env python3
"""
approve_signup.py — review and act on ScanGuru signup requests.

Run from the portal-api repo root (where the `app/` package lives), with the
same environment your API uses (DATABASE_URL, SMTP_* set).

    python approve_signup.py --init-db            # create the table (first run only)
    python approve_signup.py --list               # show pending requests
    python approve_signup.py --approve <id>       # create the account + email temp password
    python approve_signup.py --approve <id> --role radiologist
    python approve_signup.py --reject  <id> --note "reason (optional)"

Approval creates (or reuses) an Org named after the applicant's organization,
creates a User with a generated temporary password, marks the request approved,
and emails the applicant their login + temp password. It reuses the app's own
hash_password() so the credentials verify against /login.
"""
from __future__ import annotations
import argparse
import secrets
import sys

# form role text -> portal UserRole (default radiologist: the full clinical view)
_ROLE_MAP = {
    "radiologist": "radiologist",
    "clinician / physician": "radiologist",
    "clinician": "radiologist",
    "physician": "radiologist",
    "researcher": "radiologist",
    "student / trainee": "radiologist",
    "it / administration": "technologist",
    "other": "radiologist",
}


def map_role(role_text: str, UserRole):
    return getattr(UserRole, _ROLE_MAP.get((role_text or "").strip().lower(), "radiologist"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-db", action="store_true", help="create the signup_requests table")
    ap.add_argument("--list", action="store_true", help="list pending requests")
    ap.add_argument("--approve", metavar="ID")
    ap.add_argument("--reject", metavar="ID")
    ap.add_argument("--role", default=None, help="override portal role on approval")
    ap.add_argument("--note", default=None, help="review note (stored; emailed on reject)")
    args = ap.parse_args()

    try:
        from app.database import SessionLocal, engine
        from app.auth import hash_password
        from app.mailer import send_email
        from app.config import settings
        from app.models import Org, User, UserRole, AccessMode
        from app.signup_models import SignupRequest
    except ModuleNotFoundError as e:
        sys.exit("Cannot import the app package (%s). Run from the portal-api repo root." % e)

    if args.init_db:
        SignupRequest.__table__.create(engine, checkfirst=True)
        print("signup_requests table ready.")
        return

    db = SessionLocal()
    try:
        if args.list or (not args.approve and not args.reject):
            rows = (db.query(SignupRequest)
                      .filter(SignupRequest.status == "pending")
                      .order_by(SignupRequest.created_at.asc()).all())
            if not rows:
                print("No pending requests."); return
            print("Pending requests:\n")
            for r in rows:
                print("  %s  %s %s  <%s>" % (r.id, r.first_name, r.last_name, r.email))
                print("      org=%s  role=%s  country=%s  interest=%s"
                      % (r.org_name, r.role_text, r.country, r.interest or "-"))
                print("      approve: python approve_signup.py --approve %s\n" % r.id)
            return

        if args.reject:
            r = db.get(SignupRequest, args.reject)
            if not r: sys.exit("No request with id %s" % args.reject)
            if r.status != "pending":
                print("Request already %s; nothing to do." % r.status); return
            r.status = "rejected"; r.review_note = args.note
            from sqlalchemy import func as _f
            r.reviewed_at = _f.now()
            db.commit()
            print("Rejected %s (%s)." % (r.email, r.id))
            if settings.smtp_host:
                send_email(r.email, "ScanGuru access request",
                           "Hello %s,\n\nThank you for your interest in ScanGuru. We're unable to "
                           "set up an account at this time.%s\n\nWarm regards,\nScanGuru Team\n"
                           % (r.first_name, ("\n\n" + args.note) if args.note else ""))
            return

        # --- approve ---
        r = db.get(SignupRequest, args.approve)
        if not r: sys.exit("No request with id %s" % args.approve)
        if r.status != "pending":
            print("Request already %s; nothing to do." % r.status); return

        existing = db.query(User).filter(User.email == r.email).first()
        if existing:
            print("A user with %s already exists (%s). Marking request approved, no new account."
                  % (r.email, existing.id))
            r.status = "approved"; r.created_user_id = existing.id
            from sqlalchemy import func as _f
            r.reviewed_at = _f.now(); r.review_note = args.note
            db.commit(); return

        org = db.query(Org).filter(Org.name == r.org_name).first()
        if org is None:
            org = Org(name=r.org_name, region="us", access_mode=AccessMode.shared)
            db.add(org); db.flush()
            print("created org : %s (%s)" % (org.name, org.id))
        else:
            print("reused  org : %s (%s)" % (org.name, org.id))

        role = getattr(UserRole, args.role) if args.role else map_role(r.role_text, UserRole)
        temp_pw = secrets.token_urlsafe(9)
        user = User(
            org_id=org.id, email=r.email, password_hash=hash_password(temp_pw),
            full_name=("%s %s" % (r.first_name, r.last_name)).strip(),
            role=role, is_active=True, totp_enabled=False,
        )
        db.add(user); db.flush()

        r.status = "approved"; r.created_user_id = user.id; r.review_note = args.note
        from sqlalchemy import func as _f
        r.reviewed_at = _f.now()
        db.commit()

        print("\napproved: %s  role=%s  org=%s" % (r.email, role.value, org.name))
        print("temp password: %s" % temp_pw)

        sent = send_email(
            r.email, "Your ScanGuru account is ready",
            "Hello %s,\n\nYour ScanGuru account has been approved.\n\n"
            "Sign in at: %s\nEmail: %s\nTemporary password: %s\n\n"
            "Please change your password after your first sign-in.\n\n"
            "ScanGuru is provided for research and evaluation, and is built to support "
            "a qualified clinician's judgment — not replace it.\n\nWarm regards,\nScanGuru Team\n"
            % (r.first_name, settings.portal_login_url, r.email, temp_pw),
        )
        print("welcome email: %s" % ("sent" if sent else "NOT sent (SMTP unconfigured — share the temp password manually)"))
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
