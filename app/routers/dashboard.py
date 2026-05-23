"""Dashboard endpoints — drives the stat cards."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access import can_see_all_org_data
from app.auth import get_current_user
from app.database import get_db
from app.models import Study, StudyStatus, Urgency, User
from app.schemas import DashboardStats

router = APIRouter()


@router.get("/stats", response_model=DashboardStats)
def stats(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    """Counts for the dashboard stat cards.

    In shared-clinic mode (org default): every user sees org-wide counts.
    In per_doctor mode: non-admin radiologists see only their assigned studies.
    """
    base = select(func.count(Study.id))
    if can_see_all_org_data(db, current):
        base = base.where(Study.org_id == current.org_id)
    else:
        base = base.where(Study.assigned_radiologist == current.id)

    def count(q):
        return db.execute(q).scalar_one()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    return DashboardStats(
        critical=count(base.where(Study.status == StudyStatus.critical)),
        urgent=count(base.where(
            Study.urgency == Urgency.urgent,
            Study.status != StudyStatus.completed,
        )),
        pending=count(base.where(Study.status == StudyStatus.awaiting_review)),
        routine=count(base.where(
            Study.urgency == Urgency.routine,
            Study.status != StudyStatus.completed,
        )),
        completed_today=count(base.where(
            Study.status == StudyStatus.completed,
            Study.study_datetime >= today_start,
        )),
    )
