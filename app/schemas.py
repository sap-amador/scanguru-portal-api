"""Pydantic request and response models for all endpoints."""
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models import StudyStatus, Urgency, Modality, UserRole


# --- Auth ---
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    avatar_url: str | None = None
    org_id: uuid.UUID

    class Config:
        from_attributes = True


# --- Dashboard ---
class DashboardStats(BaseModel):
    critical: int
    urgent: int
    pending: int
    routine: int
    completed_today: int


# --- Patient ---
class PatientCreate(BaseModel):
    mrn: str
    name: str
    dob: datetime | None = None
    sex: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    emergency_contact: str | None = None


class PatientOut(BaseModel):
    id: uuid.UUID
    visible_id: str
    mrn: str
    name: str
    dob: datetime | None = None
    sex: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None


class PatientSummary(BaseModel):
    """Compact patient ref used in study list rows."""
    id: uuid.UUID
    visible_id: str
    name: str


# --- Study ---
class StudyOut(BaseModel):
    id: uuid.UUID
    patient: PatientSummary
    modality: Modality
    status: StudyStatus
    urgency: Urgency
    primary_finding: str | None = None
    confidence: float | None = None
    study_datetime: datetime


class StudyListResponse(BaseModel):
    items: list[StudyOut]
    total: int
    page: int
    page_size: int


class StudyCreateResponse(BaseModel):
    study_id: uuid.UUID
    status: StudyStatus
    primary_finding: str | None = None
    confidence: float | None = None


class ReviewRequest(BaseModel):
    clinician_notes: str | None = None


# --- Patient profile / timeline ---
class TimelineStudy(BaseModel):
    id: uuid.UUID
    modality: Modality
    primary_finding: str | None
    confidence: float | None
    urgency: Urgency
    status: StudyStatus
    study_datetime: datetime


class PatientTimeline(BaseModel):
    patient: PatientOut
    total_studies: int
    critical_count: int
    modalities_count: int
    studies: list[TimelineStudy]


# --- Org / Heal-for-All free tier ---
class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    region: str
    free_tier_type: str | None = None
    free_tier_verified: bool
    monthly_scan_quota: int

    class Config:
        from_attributes = True


class UsageOut(BaseModel):
    period_month: str
    scans_used: int
    quota: int
    warn: bool
    over: bool


class FreeTierApplyIn(BaseModel):
    free_tier_type: str          # low_income_country | rural_clinic | pediatric_oncology | charity
    note: str | None = None
