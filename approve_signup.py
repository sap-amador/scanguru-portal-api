#!/usr/bin/env python3
"""
approve_signup.py — terminal fallback for the review page (scanguru.ai/approve.html).

    python approve_signup.py --init-db
    python approve_signup.py --list
    python approve_signup.py --approve <id> [--role radiologist] [--note "..."]
    python approve_signup.py --reject  <id> [--note "..."]
    python approve_signup.py --link    <id>      # print a fresh review link for the page
"""
from __future__ import annotations
import argparse, sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-db", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--approve", metavar="ID")
    ap.add_argument("--reject", metavar="ID")
    ap.add_argument("--link", metavar="ID")
    ap.add_argument("--role", default=None)
    ap.add_argument("--note", default=None)
    args = ap.parse_args()

    try:
        from app.database import SessionLocal, engine
        from app.signup_models import SignupRequest
        from app.signup_admin import approve_request, reject_request, review_link
    except ModuleNotFoundError as e:
        sys.exit("Cannot import the app package (%s). Run from the portal-api repo root." % e)

    if args.init_db:
        SignupRequest.__table__.create(engine, checkfirst=True); print("signup_requests table ready."); return

    db = SessionLocal()
    try:
        if args.link:
            print(review_link(args.link)); return

        if args.list or not (args.approve or args.reject):
            rows = (db.query(SignupRequest).filter(SignupRequest.status == "pending")
                      .order_by(SignupRequest.created_at.asc()).all())
            if not rows:
                print("No pending requests."); return
            print("Pending requests:\n")
            for r in rows:
                print("  %s  %s %s  <%s>" % (r.id, r.first_name, r.last_name, r.email))
                print("      org=%s  role=%s  country=%s  interest=%s" % (r.org_name, r.role_text, r.country, r.interest or "-"))
                print("      review page: %s\n" % review_link(r.id))
            return

        rid = args.approve or args.reject
        r = db.get(SignupRequest, rid)
        if not r:
            sys.exit("No request with id %s" % rid)
        res = approve_request(db, r, note=args.note, role_override=args.role) if args.approve else reject_request(db, r, note=args.note)
        if not res.get("ok"):
            print(res.get("error")); return
        print("%s: %s" % (res["action"], res["email"]))
        if res.get("temp_password"):
            print("org: %s  role: %s" % (res.get("org"), res.get("role")))
            print("temp password: %s" % res["temp_password"])
        print("email: %s" % ("sent" if res.get("email_sent") else "NOT sent (SMTP unconfigured or failed)"))
        if res.get("message"):
            print(res["message"])
    finally:
        db.close()


if __name__ == "__main__":
    main()
