#!/usr/bin/env python3
"""
Create app/routers/patients.py with:
  GET  /api/v1/patients         — paginated list + search (?q=, ?page=, ?page_size=)
  POST /api/v1/patients         — create a new patient (admin/radiologist/technologist)
  GET  /api/v1/patients/{id}    — single patient (already exists in studies router)

Also registers the router in app/main.py if not already wired.

Run from your scanguru-portal repo root:
    python3 scripts/patch_add_patients_router.py

It backs up any file it touches first.
"""
import os, re, shutil, sys
from datetime import datetime

ROUTER_PATH = "app/routers/patients.py"
MAIN_PATH = "app/main.py"

if not os.path.exists(MAIN_PATH):
    sys.exit(f"ERROR: {MAIN_PATH} not found. Run from your scanguru-portal repo root.")

# --- 1) Create app/routers/patients.py if it doesn't exist ---
if os.path.exists(ROUTER_PATH):
    backup = ROUTER_PATH.replace('.py', f'_backup_{datetime.now().strftime("%Y%m%d_%H%M")}.py')
    shutil.copy(ROUTER_PATH, backup)
    print(f"Existing patients.py backed up to {backup}")

ROUTER_CONTENT = '''"""Patients router — list, search, create."""
from typing import Annotated, Optional, List
from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select, func, or_, desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Patient, Study, User
from app.auth import get_current_user
from app.access import can_see_all_org_data
from app.audit import audit
from app.utils.visible_id import next_patient_visible_id
from app.crypto import encrypt_phi


router = APIRouter(prefix="/patients", tags=["patients"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PatientListItem(BaseModel):
    id: uuid.UUID
    visible_id: str
    name: str
    mrn: str
    dob: Optional[str] = None
    sex: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
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
    dob: Optional[str] = None         # YYYY-MM-DD; stored as date in DB
    sex: Optional[str] = None         # 'M' | 'F' | 'O'
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


class PatientCreateResponse(BaseModel):
    id: uuid.UUID
    visible_id: str
    name: str
    mrn: str


# ---------------------------------------------------------------------------
# GET /patients — paginated list + search
# ---------------------------------------------------------------------------

@router.get("", response_model=PatientListResponse)
def list_patients(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: Optional[str] = Query(None, description="Search by name, MRN, or visible_id"),
):
    """List patients in the user's org with optional fuzzy search."""
    # Subquery: per-patient study counts + last study time
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
        # Patient name/phone/email/address are encrypted at rest, so we cannot
        # ILIKE them in SQL. Match on MRN + visible_id in SQL; name match is
        # handled in Python after decrypt (acceptable for org-scoped queries).
        like = f"%{q}%"
        base_sql_filter = or_(
            Patient.mrn.ilike(like),
            Patient.visible_id.ilike(like),
        )
        # We will combine SQL pre-filter (MRN/visible_id) with Python post-filter
        # (name) below. For very large orgs this gets refactored to use a
        # search_vector column; v1 keeps it simple.
        all_rows = db.execute(
            base.order_by(desc(Patient.created_at))
        ).all()
        q_lower = q.lower()
        matched = []
        for patient, sc, lsa in all_rows:
            # Patient.name is decrypted on attribute access via the hybrid setter
            name = (patient.name or "").lower()
            mrn = (patient.mrn or "").lower()
            vid = (patient.visible_id or "").lower()
            if q_lower in name or q_lower in mrn or q_lower in vid:
                matched.append((patient, sc, lsa))
        total = len(matched)
        # Paginate the in-memory list
        start = (page - 1) * page_size
        rows = matched[start:start + page_size]
    else:
        # Count total
        total = db.execute(
            select(func.count(Patient.id)).where(Patient.org_id == current.org_id)
        ).scalar_one()
        # Page the SQL query directly
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
            name=patient.name or "",
            mrn=patient.mrn,
            dob=patient.dob.isoformat() if patient.dob else None,
            sex=patient.sex,
            phone=patient.phone,
            email=patient.email,
            address=patient.address,
            study_count=int(study_count or 0),
            last_study_at=last_study_at,
            created_at=patient.created_at,
        ))

    return PatientListResponse(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# POST /patients — create a patient (no scan required)
# ---------------------------------------------------------------------------

@router.post("", response_model=PatientCreateResponse, status_code=status.HTTP_201_CREATED)
def create_patient(
    body: PatientCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    """Create a new patient row. MRN must be unique within the org."""
    if current.role not in ("admin", "radiologist", "technologist"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role to create patients")

    if not body.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Patient name is required")
    if not body.mrn.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MRN is required")

    # Uniqueness: same MRN can exist in different orgs but not within one
    existing = db.execute(
        select(Patient).where(
            Patient.org_id == current.org_id,
            Patient.mrn == body.mrn.strip(),
        )
    ).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A patient with MRN {body.mrn} already exists in this org")

    # Parse DOB if provided
    dob_value = None
    if body.dob:
        try:
            dob_value = datetime.strptime(body.dob, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "DOB must be in YYYY-MM-DD format")

    # Generate the human-readable visible_id (PT-YYYY-NNNN)
    visible_id = next_patient_visible_id(db, current.org_id)

    patient = Patient(
        id=uuid.uuid4(),
        org_id=current.org_id,
        visible_id=visible_id,
        mrn=body.mrn.strip(),
        name=body.name.strip(),
        dob=dob_value,
        sex=body.sex,
        phone=body.phone,
        email=body.email,
        address=body.address,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)

    audit(db, current, "patient.create", "patient", patient.id,
          ip=request.client.host if request.client else None)

    return PatientCreateResponse(
        id=patient.id,
        visible_id=patient.visible_id,
        name=patient.name or "",
        mrn=patient.mrn,
    )
'''

# Write only if it does not exist OR existing content lacks our endpoints
need_write = True
if os.path.exists(ROUTER_PATH):
    with open(ROUTER_PATH) as f:
        existing = f.read()
    if 'def list_patients' in existing and 'def create_patient' in existing:
        print(f"{ROUTER_PATH} already has list_patients + create_patient — skipping write")
        need_write = False

if need_write:
    with open(ROUTER_PATH, 'w') as f:
        f.write(ROUTER_CONTENT)
    print(f"Wrote {ROUTER_PATH} ({len(ROUTER_CONTENT)} bytes)")

# --- 2) Register the router in app/main.py ---
with open(MAIN_PATH) as f:
    main_src = f.read()

if 'from app.routers import patients' in main_src or 'routers.patients' in main_src:
    print(f"{MAIN_PATH} already imports the patients router — skipping main.py patch")
else:
    backup = MAIN_PATH.replace('.py', f'_backup_{datetime.now().strftime("%Y%m%d_%H%M")}.py')
    shutil.copy(MAIN_PATH, backup)
    print(f"{MAIN_PATH} backed up to {backup}")

    # Try to add the import next to the existing `from app.routers import` line
    if 'from app.routers import' in main_src:
        # Find that line and add patients to it (or add a sibling import below)
        m = re.search(r'from app\.routers import ([^\n]+)\n', main_src)
        if m and 'patients' not in m.group(1):
            new_imports = m.group(1).rstrip()
            if new_imports.endswith(','):
                new_imports = new_imports + ' patients'
            else:
                new_imports = new_imports + ', patients'
            main_src = main_src[:m.start()] + f'from app.routers import {new_imports}\n' + main_src[m.end():]
            print('Added `patients` to existing `from app.routers import …` line')
        else:
            # already has it somehow — skip
            pass
    else:
        # Fallback: add a fresh import after the last `from app.` line
        main_src = main_src.replace(
            'app = FastAPI(',
            'from app.routers import patients\n\napp = FastAPI(',
            1,
        )
        print('Added fresh `from app.routers import patients` import line')

    # Add app.include_router(patients.router, prefix="/api/v1")
    # Look for an existing include_router line as anchor
    include_pattern = re.compile(r'(app\.include_router\([a-z_.]+\.router[^\n]*\n)')
    matches = list(include_pattern.finditer(main_src))
    if matches and 'patients.router' not in main_src:
        # Insert ours right after the last existing include_router line
        last = matches[-1]
        insertion = 'app.include_router(patients.router, prefix="/api/v1")\n'
        main_src = main_src[:last.end()] + insertion + main_src[last.end():]
        print('Added `app.include_router(patients.router, prefix="/api/v1")` line')
    elif 'patients.router' in main_src:
        print('patients.router already registered — skipping')
    else:
        print('WARNING: could not find an anchor app.include_router(...) line.')
        print('Add this line manually to main.py:')
        print('    app.include_router(patients.router, prefix="/api/v1")')

    with open(MAIN_PATH, 'w') as f:
        f.write(main_src)
    print(f"Updated {MAIN_PATH}")

print()
print('Done. Restart uvicorn — the new endpoints will live at:')
print('  GET  /api/v1/patients?page=1&page_size=20&q=…')
print('  POST /api/v1/patients   (JSON body: name, mrn, dob?, sex?, phone?, email?, address?)')
print()
print('If your main.py registers routers some other way (e.g., dynamic discovery),')
print('verify the new router is loaded by checking /docs after restart.')
