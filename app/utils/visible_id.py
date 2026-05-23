"""Generate human-readable patient IDs: PT-YYYY-####, per-org per-year."""
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import VisibleIdCounter


def next_visible_id(db: Session, org_id: uuid.UUID) -> str:
    """
    Atomically increment the per-org per-year counter and return 'PT-YYYY-####'.

    Caller is responsible for committing the surrounding transaction; this
    function uses SELECT ... FOR UPDATE so concurrent inserts don't race.
    """
    year = datetime.utcnow().year
    counter = db.execute(
        select(VisibleIdCounter)
        .where(VisibleIdCounter.org_id == org_id, VisibleIdCounter.year == year)
        .with_for_update()
    ).scalar_one_or_none()

    if counter is None:
        counter = VisibleIdCounter(org_id=org_id, year=year, counter=1)
        db.add(counter)
        db.flush()
    else:
        counter.counter += 1
        db.flush()

    return f"PT-{year}-{counter.counter:04d}"
