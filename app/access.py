"""Centralized access-control helper for the two clinic modes.

Used everywhere a router needs to decide whether the current user can see
all org data or only their assigned subset. Keeps the policy in one place
so flipping `orgs.access_mode` from 'shared' to 'per_doctor' is the only
operational change needed.
"""
from sqlalchemy.orm import Session

from app.models import Org, User, UserRole, AccessMode


def can_see_all_org_data(db: Session, user: User) -> bool:
    """True when the user can access every patient/study/report in their org.

    Admins always pass. Non-admin users pass when their org's access_mode
    is 'shared' (small-clinic flow). In 'per_doctor' mode, non-admins
    fall back to per-assignment scoping at the call site.
    """
    if user.role == UserRole.admin:
        return True
    org = db.get(Org, user.org_id)
    return bool(org and org.access_mode == AccessMode.shared)
