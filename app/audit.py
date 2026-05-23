"""Audit log helper. Call audit(...) after any state-changing action or sensitive read."""
from typing import Optional
import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def audit(
    db: Session,
    actor: Optional[User],
    action: str,
    resource_type: str,
    resource_id: Optional[uuid.UUID] = None,
    *,
    success: bool = True,
    extra: Optional[dict] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """
    Write an audit row. Commits its own transaction so the audit
    persists even if the caller's transaction rolls back.
    """
    row = AuditLog(
        org_id=actor.org_id if actor else None,
        actor_user_id=actor.id if actor else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip,
        user_agent=user_agent,
        success=success,
        extra=extra,
    )
    db.add(row)
    db.commit()
