"""SignupRequest table — durable record of every /signup submission.

Kept in its own module so it can be imported both by the public route and by
the approve_signup.py CLI without touching the core models file. Reuses the
same declarative Base, so it registers on the same metadata.
"""
from __future__ import annotations
import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class SignupRequest(Base):
    __tablename__ = "signup_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(120))
    last_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), index=True)      # not unique: re-applies allowed
    org_name: Mapped[str] = mapped_column(String(200))
    role_text: Mapped[str] = mapped_column(String(80))               # free text from the form
    country: Mapped[str] = mapped_column(String(120))
    interest: Mapped[str | None] = mapped_column(String(120), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_signup_status_created", "status", "created_at"),)
