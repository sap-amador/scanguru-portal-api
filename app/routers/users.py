"""Directory of users in the caller's organisation.

Exists so the patient-assignment picker has something to populate from. Mounted
at /api/v1/users (see main.py).

Deliberately does not filter by role. Which roles actually treat patients
varies by site — a small clinic may run everything through one account, a
hospital may separate radiologist from referring physician — so the endpoint
returns every active user with their role attached and lets the caller present
that choice. Encoding a clinical policy here would be guessing at how each
pilot site works.
"""
import uuid
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import get_current_user
from app.database import get_db
from app.models import User, UserRole
from sqlalchemy.orm import Session


router = APIRouter()


class OrgUserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    role: UserRole
    is_self: bool = False


class OrgUserListResponse(BaseModel):
    items: List[OrgUserOut]
    total: int


@router.get("", response_model=OrgUserListResponse)
def list_org_users(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    role: Optional[UserRole] = Query(None, description="Filter to a single role"),
    include_inactive: bool = Query(False),
):
    """Active users in the caller's org, ordered by name.

    Org-scoped by the same rule as everything else: a user can only ever see
    colleagues inside their own organisation.
    """
    stmt = select(User).where(User.org_id == current.org_id)
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    if role is not None:
        stmt = stmt.where(User.role == role)

    rows = db.execute(stmt.order_by(User.full_name)).scalars().all()

    items = [
        OrgUserOut(
            id=u.id,
            full_name=u.full_name,
            email=u.email,
            role=u.role,
            is_self=(u.id == current.id),
        )
        for u in rows
    ]
    return OrgUserListResponse(items=items, total=len(items))
