"""SQLAlchemy 2.0 models for the ScanGuru portal backend."""
from __future__ import annotations
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    String, Text, DateTime, ForeignKey, Index, UniqueConstraint,
    Enum as SAEnum, Float, Boolean, LargeBinary, Integer, func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --- Enums ---
class AccessMode(str, PyEnum):
    """Per-org access policy.

    shared      — small-clinic default: every clinician sees every patient.
    per_doctor  — large-org / hospital: radiologists see only their assigned patients.
    """
    shared = "shared"
    per_doctor = "per_doctor"


class UserRole(str, PyEnum):
    admin = "admin"
    radiologist = "radiologist"
    technologist = "technologist"
    referring = "referring"


class StudyStatus(str, PyEnum):
    queued = "queued"
    processing = "processing"
    awaiting_review = "awaiting_review"
    completed = "completed"
    critical = "critical"
    failed = "failed"


class Urgency(str, PyEnum):
    routine = "routine"
    urgent = "urgent"
    stat = "stat"


class Modality(str, PyEnum):
    CXR = "CXR"
    CT_BRAIN = "CT_BRAIN"
    CT_CHEST = "CT_CHEST"
    MAMMO = "MAMMO"
    DENTAL = "DENTAL"
    MSK = "MSK"
    MRI_SPINE = "MRI_SPINE"
    MRI_KNEE = "MRI_KNEE"
    MRI_PROSTATE = "MRI_PROSTATE"
    MRI_BREAST = "MRI_BREAST"
    MRI_CARDIAC = "MRI_CARDIAC"
    MRI_SHOULDER = "MRI_SHOULDER"
    US = "US"
    PET = "PET"


# --- Core tables ---
class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str] = mapped_column(String(8), default="us")  # us | eu — data residency
    access_mode: Mapped[AccessMode] = mapped_column(
        SAEnum(AccessMode),
        default=AccessMode.shared,
        server_default="shared",  # ensures existing rows backfill cleanly
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="org")


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole))
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    org: Mapped["Org"] = relationship(back_populates="users")


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), index=True)
    visible_id: Mapped[str] = mapped_column(String(20), index=True)  # PT-2024-0847
    mrn: Mapped[str] = mapped_column(String(64))
    # PHI columns — encrypted at app layer via Fernet
    name_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    phone_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    email_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    address_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    emergency_contact_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    dob: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sex: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("org_id", "mrn", name="uq_patient_org_mrn"),
        UniqueConstraint("org_id", "visible_id", name="uq_patient_org_visible_id"),
    )


class PatientAssignment(Base):
    """Maps doctors to patients. Used only when an org's access_mode is per_doctor."""
    __tablename__ = "patient_assignments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    assignment_type: Mapped[str] = mapped_column(String(32), default="primary")  # primary | consulting
    assigned_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_assign_doctor_active", "doctor_user_id", "revoked_at"),)


class Study(Base):
    __tablename__ = "studies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    modality: Mapped[Modality] = mapped_column(SAEnum(Modality))
    status: Mapped[StudyStatus] = mapped_column(SAEnum(StudyStatus), default=StudyStatus.queued, index=True)
    urgency: Mapped[Urgency] = mapped_column(SAEnum(Urgency), default=Urgency.routine, index=True)
    ordered_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    assigned_radiologist: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    source_image_key: Mapped[str] = mapped_column(Text)            # storage key, not URL
    source_image_sha256: Mapped[str] = mapped_column(String(64))   # cache key + integrity
    study_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        Index("ix_studies_dashboard", "assigned_radiologist", "status", "study_datetime"),
        Index("ix_studies_org_status", "org_id", "status"),
    )


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"), unique=True, index=True)
    prediction_json: Mapped[dict] = mapped_column(JSONB)
    primary_finding: Mapped[str] = mapped_column(String(200))
    confidence: Mapped[float] = mapped_column(Float)
    pdf_key: Mapped[str] = mapped_column(Text)
    heatmap_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_service_version: Mapped[str] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clinician_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PatientInsurance(Base):
    __tablename__ = "patient_insurance"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    provider: Mapped[str] = mapped_column(String(200))
    policy_number_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    group_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscriber_name_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClinicalNote(Base):
    __tablename__ = "clinical_notes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    study_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("studies.id"), nullable=True, index=True)
    author_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VisibleIdCounter(Base):
    """Per-org, per-year sequence for human-readable patient IDs (PT-YYYY-####)."""
    __tablename__ = "visible_id_counters"
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    counter: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orgs.id"), nullable=True, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))          # study.create, report.read, auth.login, ...
    resource_type: Mapped[str] = mapped_column(String(32))   # study | report | patient | user
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
