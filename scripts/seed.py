"""Bootstrap: create an Org and an initial admin user.

Run after `alembic upgrade head`:
    python scripts/seed.py --org "Demo Clinic" --email admin@demo.test \\
        --password "ChangeMe123!" --name "Admin User" [--access-mode shared|per_doctor]
"""
import argparse
import sys

from app.auth import hash_password
from app.database import SessionLocal
from app.models import Org, User, UserRole, AccessMode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True, help="Organization name")
    parser.add_argument("--region", default="us", help="Data residency region (us | eu)")
    parser.add_argument("--access-mode", default="shared",
                        choices=["shared", "per_doctor"],
                        help="Org access mode. 'shared' (default) suits small clinics where "
                             "every clinician sees every patient. 'per_doctor' restricts "
                             "radiologists to their assigned patients.")
    parser.add_argument("--email", required=True, help="Admin user email")
    parser.add_argument("--password", required=True, help="Admin user password")
    parser.add_argument("--name", default="Admin User", help="Admin full name")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == args.email.lower()).first()
        if existing:
            print(f"User {args.email} already exists (id={existing.id}). Nothing to do.")
            sys.exit(0)

        org = Org(
            name=args.org,
            region=args.region,
            access_mode=AccessMode(args.access_mode),
        )
        db.add(org)
        db.flush()

        user = User(
            org_id=org.id,
            email=args.email.lower(),
            password_hash=hash_password(args.password),
            full_name=args.name,
            role=UserRole.admin,
            is_active=True,
        )
        db.add(user)
        db.commit()

        print("Seeded successfully:")
        print(f"  org_id       = {org.id}")
        print(f"  access_mode  = {org.access_mode.value}")
        print(f"  user_id      = {user.id}")
        print(f"  email        = {user.email}")
        print(f"  role         = {user.role.value}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
