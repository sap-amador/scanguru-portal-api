"""Account self-service — authenticated password change.

Closes the gap left by the temp-password approval flow: a user who received a
generated password can now set their own. Additive router; mount it under the
existing /api/v1/auth prefix in main.py (see WIRING note). Reuses the app's own
verify_password / hash_password so nothing about the hashing scheme changes.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import verify_password, hash_password, get_current_user
from app.database import get_db
from app.models import User
from app.ratelimit import limiter

router = APIRouter()


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _min_len(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("New password must be at least 8 characters.")
        return v


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")  # per-IP: throttle current-password guessing
def change_password(
    body: ChangePasswordIn,
    request: Request,
    current: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    ip = request.client.host if request.client else None

    if not verify_password(body.current_password, current.password_hash):
        audit(db, current, "auth.change_password.failed", "user", current.id,
              success=False, ip=ip)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")

    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "New password must differ from the current one.")

    current.password_hash = hash_password(body.new_password)
    db.add(current)
    db.commit()

    audit(db, current, "auth.change_password", "user", current.id, ip=ip)
    return None  # 204 No Content
