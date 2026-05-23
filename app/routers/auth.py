"""Auth endpoints: /login and /me."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import (
    verify_password, create_access_token, get_current_user, verify_totp,
)
from app.audit import audit
from app.database import get_db
from app.models import User
from app.schemas import LoginRequest, TokenResponse, UserOut

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    ip = request.client.host if request.client else None

    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        audit(db, None, "auth.login.failed", "user", None,
              success=False, extra={"email": body.email}, ip=ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if user.totp_enabled:
        if not body.totp_code or not verify_totp(user.totp_secret or "", body.totp_code):
            audit(db, user, "auth.login.totp_failed", "user", user.id, success=False, ip=ip)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid TOTP code")

    token = create_access_token(str(user.id), str(user.org_id), user.role.value)
    audit(db, user, "auth.login.success", "user", user.id, ip=ip)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
def me(current: Annotated[User, Depends(get_current_user)]):
    return current
