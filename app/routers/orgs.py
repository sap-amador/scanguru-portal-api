"""Org profile, the free-tier usage meter, and the Heal-for-All application.

Mounted at /api/v1/orgs (see main.py). Uses the existing get_current_user
dependency and the existing audit() helper — nothing new in auth.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import get_current_user
from app.database import get_db
from app.models import Org, User, UserRole
from app import quota
from app.schemas import OrgOut, UsageOut, FreeTierApplyIn

router = APIRouter()


@router.get("/me", response_model=OrgOut)
def my_org(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    return db.get(Org, current.org_id)


@router.get("/me/usage", response_model=UsageOut)
def my_usage(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    """Drives the in-app usage meter (read-only; no increment)."""
    org = db.get(Org, current.org_id)
    r = quota.current_usage(db, org)
    return UsageOut(
        period_month=str(quota.first_of_month_utc()),
        scans_used=r.used, quota=r.quota, warn=r.warn, over=r.over,
    )


@router.post("/free-tier/apply", status_code=status.HTTP_202_ACCEPTED)
def apply_free_tier(
    body: FreeTierApplyIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    """Submit a Heal-for-All free-tier application -> pending human verification.

    Records the requested type; a ScanGuru reviewer flips free_tier_verified
    out-of-band (see INTEGRATION.md). An org admin applying does NOT self-verify
    — verification is deliberately a separate, staff step.
    """
    if current.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an org admin can apply for free access")

    valid = {"low_income_country", "rural_clinic", "pediatric_oncology", "charity"}
    if body.free_tier_type not in valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"free_tier_type must be one of: {', '.join(sorted(valid))}",
        )

    org = db.get(Org, current.org_id)
    org.free_tier_type = body.free_tier_type
    db.commit()

    audit(db, current, "free_tier.apply", "org", org.id,
          ip=(request.client.host if request.client else None),
          extra={"type": body.free_tier_type, "note": body.note})

    return {"status": "submitted",
            "detail": "A ScanGuru reviewer will verify your institution shortly."}
