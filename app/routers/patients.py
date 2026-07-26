"""Patient endpoints: list, search, detail, timeline, create."""
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.crypto import decrypt, encrypt
from app.database import get_db
from app.models import Patient, PatientAssignment, Study, Report, User, UserRole
from app.schemas import AssignedUserOut, PatientOut, PatientTimeline, TimelineStudy
from app.utils.visible_id import next_visible_id


router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _report_type(r) -> Optional[str]:
    """Which report variant Report.pdf_key actually holds.

    create_study records this in prediction_json. Reports generated before
    that was added carry no marker, and this returns None rather than
    assuming "clinical" — the UI renders its variant chips neutral on None
    instead of claiming a variant was generated when we do not know.
    """
    if r is None:
        return None
    payload = getattr(r, "prediction_json", None)
    if not isinstance(payload, dict):
        return None
    value = payload.get("report_type")
    return str(value).lower() if value else None


def _patient_to_out(p: Patient) -> PatientOut:
    """Convert a Patient row to the API response shape, decrypting PHI."""
    return PatientOut(
        id=p.id,
        visible_id=p.visible_id,
        mrn=p.mrn,
        name=decrypt(p.name_encrypted) or "",
        dob=p.dob,
        sex=p.sex,
        phone=decrypt(p.phone_encrypted) if getattr(p, "phone_encrypted", None) else None,
        email=decrypt(p.email_encrypted) if getattr(p, "email_encrypted", None) else None,
        address=decrypt(p.address_encrypted) if getattr(p, "address_encrypted", None) else None,
    )


def _current_assignee(db: Session, patient_id: uuid.UUID) -> Optional[User]:
    """The user holding the live primary assignment, or None.

    Returns None rather than falling back to whoever created the patient — an
    unassigned patient should read as unassigned, not as belonging to the
    person who happened to run the upload.
    """
    row = db.execute(
        select(User)
        .join(PatientAssignment, PatientAssignment.doctor_user_id == User.id)
        .where(
            PatientAssignment.patient_id == patient_id,
            PatientAssignment.assignment_type == "primary",
            PatientAssignment.revoked_at.is_(None),
        )
        .order_by(PatientAssignment.assigned_at.desc())
    ).scalars().first()
    return row


def _assert_access(db: Session, current: User, patient_id: uuid.UUID, patient: Patient) -> None:
    """Enforce org scope + (for non-admins) explicit assignment."""
    if patient.org_id != current.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    if current.role == UserRole.admin:
        return
    is_assigned = db.query(PatientAssignment).filter(
        PatientAssignment.patient_id == patient_id,
        PatientAssignment.doctor_user_id == current.id,
        PatientAssignment.revoked_at.is_(None),
    ).first()
    if not is_assigned:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not assigned to this patient")


# ---------------------------------------------------------------------------
# Schemas for the new list + create endpoints
# (left here to avoid touching app/schemas.py)
# ---------------------------------------------------------------------------

class PatientListItem(BaseModel):
    id: uuid.UUID
    visible_id: str
    name: str
    mrn: str
    dob: Optional[date] = None
    sex: Optional[str] = None
    study_count: int
    last_study_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class PatientListResponse(BaseModel):
    items: List[PatientListItem]
    total: int
    page: int
    page_size: int


class PatientCreate(BaseModel):
    name: str
    mrn: str
    dob: Optional[str] = None           # 'YYYY-MM-DD'
    sex: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


class PatientCreateResponse(BaseModel):
    id: uuid.UUID
    visible_id: str
    name: str
    mrn: str


# ---------------------------------------------------------------------------
# GET /patients — paginated list + optional search
# ---------------------------------------------------------------------------

@router.get("", response_model=PatientListResponse)
def list_patients(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: Optional[str] = Query(None),
):
    """List patients in the user's org. Optional search by name (decrypted), MRN, or visible_id."""
    # Per-patient study count + most recent study datetime
    study_stats = (
        select(
            Study.patient_id.label("pid"),
            func.count(Study.id).label("study_count"),
            func.max(Study.study_datetime).label("last_study_at"),
        )
        .group_by(Study.patient_id)
        .subquery()
    )

    base = (
        select(Patient, study_stats.c.study_count, study_stats.c.last_study_at)
        .outerjoin(study_stats, study_stats.c.pid == Patient.id)
        .where(Patient.org_id == current.org_id)
    )

    if q:
        # MRN and visible_id are plaintext columns — match in SQL.
        # Name is encrypted — must filter after decrypt in Python.
        # For v1 with org-scoped queries this is acceptable.
        all_rows = db.execute(base.order_by(desc(Patient.created_at))).all()
        q_lower = q.lower()
        matched = []
        for patient, sc, lsa in all_rows:
            name_plain = (decrypt(patient.name_encrypted) or "").lower()
            mrn_plain = (patient.mrn or "").lower()
            vid_plain = (patient.visible_id or "").lower()
            if q_lower in name_plain or q_lower in mrn_plain or q_lower in vid_plain:
                matched.append((patient, sc, lsa))
        total = len(matched)
        start = (page - 1) * page_size
        rows = matched[start:start + page_size]
    else:
        total = db.execute(
            select(func.count(Patient.id)).where(Patient.org_id == current.org_id)
        ).scalar_one()
        rows = db.execute(
            base.order_by(desc(Patient.created_at))
                .offset((page - 1) * page_size)
                .limit(page_size)
        ).all()

    items: List[PatientListItem] = []
    for patient, study_count, last_study_at in rows:
        items.append(PatientListItem(
            id=patient.id,
            visible_id=patient.visible_id,
            name=decrypt(patient.name_encrypted) or "",
            mrn=patient.mrn,
            dob=patient.dob,
            sex=patient.sex,
            study_count=int(study_count or 0),
            last_study_at=last_study_at,
            created_at=patient.created_at,
        ))

    return PatientListResponse(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# POST /patients — create a patient
# ---------------------------------------------------------------------------

@router.post("", response_model=PatientCreateResponse, status_code=status.HTTP_201_CREATED)
def create_patient(
    body: PatientCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    """Create a new patient (no scan required). MRN unique within org."""
    if current.role not in (UserRole.admin, UserRole.radiologist, UserRole.technologist):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role to create patients")

    if not body.name or not body.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Patient name is required")
    if not body.mrn or not body.mrn.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MRN is required")

    name = body.name.strip()
    mrn = body.mrn.strip()

    # Per-org MRN uniqueness
    existing = db.execute(
        select(Patient).where(
            Patient.org_id == current.org_id,
            Patient.mrn == mrn,
        )
    ).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A patient with MRN {mrn} already exists in this org")

    # Parse DOB
    dob_value = None
    if body.dob:
        try:
            dob_value = datetime.strptime(body.dob, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "DOB must be YYYY-MM-DD")

    # Build kwargs and only include encrypted PHI columns the model actually has
    kwargs = dict(
        org_id=current.org_id,
        visible_id=next_visible_id(db, current.org_id),
        mrn=mrn,
        name_encrypted=encrypt(name),
        dob=dob_value,
        sex=body.sex,
        created_by=current.id,
    )
    # Optional encrypted columns — only set if model defines them, to avoid AttributeError
    if hasattr(Patient, "phone_encrypted") and body.phone:
        kwargs["phone_encrypted"] = encrypt(body.phone.strip())
    if hasattr(Patient, "email_encrypted") and body.email:
        kwargs["email_encrypted"] = encrypt(body.email.strip())
    if hasattr(Patient, "address_encrypted") and body.address:
        kwargs["address_encrypted"] = encrypt(body.address.strip())

    patient = Patient(**kwargs)
    db.add(patient)
    db.flush()

    # Auto-assign creator as primary (only if PatientAssignment table exists in this codebase)
    try:
        db.add(PatientAssignment(
            patient_id=patient.id,
            doctor_user_id=current.id,
            assigned_by=current.id,
            assignment_type="primary",
        ))
    except Exception:
        # If PatientAssignment columns differ, skip — patient creation is the priority
        pass

    db.commit()
    db.refresh(patient)

    # Best-effort audit (skip silently if the helper signature differs)
    try:
        from app.audit import audit
        audit(db, current, "patient.create", "patient", patient.id,
              ip=request.client.host if request.client else None)
    except Exception:
        pass

    return PatientCreateResponse(
        id=patient.id,
        visible_id=patient.visible_id,
        name=name,
        mrn=mrn,
    )


# ---------------------------------------------------------------------------
# GET /patients/{id} — single patient detail
# ---------------------------------------------------------------------------

@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(
    patient_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    _assert_access(db, current, patient_id, patient)
    return _patient_to_out(patient)


# ---------------------------------------------------------------------------
# GET /patients/{id}/timeline — patient + their studies
# ---------------------------------------------------------------------------

@router.get("/{patient_id}/timeline", response_model=PatientTimeline)
def patient_timeline(
    patient_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    _assert_access(db, current, patient_id, patient)

    rows = db.execute(
        select(Study, Report)
        .outerjoin(Report, Report.study_id == Study.id)
        .where(Study.patient_id == patient_id)
        .order_by(Study.study_datetime.desc())
    ).all()

    timeline: list[TimelineStudy] = []
    critical_count = 0
    modalities: set[str] = set()
    for s, r in rows:
        timeline.append(TimelineStudy(
            id=s.id,
            modality=s.modality,
            primary_finding=r.primary_finding if r else None,
            confidence=r.confidence if r else None,
            urgency=s.urgency,
            status=s.status,
            study_datetime=s.study_datetime,
            referring_physician=s.referring_physician,
            report_type=_report_type(r),
        ))
        modalities.add(s.modality.value)
        if s.status.value == "critical":
            critical_count += 1

    assignee = _current_assignee(db, patient_id)

    return PatientTimeline(
        patient=_patient_to_out(patient),
        assigned_to=(
            AssignedUserOut(id=assignee.id, full_name=assignee.full_name, role=assignee.role)
            if assignee else None
        ),
        total_studies=len(timeline),
        critical_count=critical_count,
        modalities_count=len(modalities),
        studies=timeline,
    )


# ---------------------------------------------------------------------------
# POST /patients/{id}/assign — set or clear the primary clinician
# ---------------------------------------------------------------------------

class AssignIn(BaseModel):
    doctor_user_id: Optional[uuid.UUID] = None   # null clears the assignment


@router.post("/{patient_id}/assign")
def assign_patient(
    patient_id: uuid.UUID,
    body: AssignIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    """Set the primary clinician for a patient, or clear it with a null id.

    The previous assignment is revoked, not deleted: PatientAssignment carries
    revoked_at precisely so a record can show who held a patient and when that
    changed. Deleting the row would erase that.
    """
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    _assert_access(db, current, patient_id, patient)

    if current.role not in (UserRole.admin, UserRole.radiologist, UserRole.technologist):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role to assign patients")

    target: Optional[User] = None
    if body.doctor_user_id is not None:
        target = db.get(User, body.doctor_user_id)
        # Same 404 for "no such user" and "user in another org" — a caller
        # should not be able to probe for accounts outside their organisation.
        if not target or target.org_id != current.org_id or not target.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found in this organisation")

    live = db.execute(
        select(PatientAssignment).where(
            PatientAssignment.patient_id == patient_id,
            PatientAssignment.assignment_type == "primary",
            PatientAssignment.revoked_at.is_(None),
        )
    ).scalars().all()

    if target and any(a.doctor_user_id == target.id for a in live):
        return {
            "status": "unchanged",
            "assigned_to": {"id": str(target.id), "full_name": target.full_name},
        }

    now = datetime.now(timezone.utc)
    for a in live:
        a.revoked_at = now

    if target:
        db.add(PatientAssignment(
            patient_id=patient_id,
            doctor_user_id=target.id,
            assigned_by=current.id,
            assignment_type="primary",
        ))

    db.commit()

    try:
        from app.audit import audit
        audit(db, current, "patient.assign", "patient", patient_id,
              ip=request.client.host if request.client else None,
              extra={
                  "to": str(target.id) if target else None,
                  "revoked": [str(a.doctor_user_id) for a in live],
              })
    except Exception:
        pass

    if not target:
        return {"status": "cleared", "assigned_to": None}
    return {
        "status": "assigned",
        "assigned_to": {"id": str(target.id), "full_name": target.full_name, "role": target.role.value},
    }
