"""Studies endpoints: list, create (upload + AI), get, signed PDF, review."""
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Request,
    UploadFile, status,
)
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access import can_see_all_org_data
from app.ai_client import analyze_image, fetch_pdf, poll_job, AIServiceError
from app.audit import audit
from app.auth import get_current_user
from app.crypto import encrypt, decrypt
from app.database import get_db
from app.models import (
    Study, Report, Patient, PatientAssignment, User,
    StudyStatus, Urgency, Modality, Org,
)
from app import quota
from app.schemas import (
    StudyListResponse, StudyOut, StudyCreateResponse, PatientSummary, ReviewRequest,
)
from app.storage import get_storage, LocalStorage, LocalStorage
from app.utils.visible_id import next_visible_id

router = APIRouter()

# Slow modalities answer with a job id instead of a finished PDF. We poll
# inline so the client still gets one complete response. Keep the ceiling
# comfortably under the platform's request timeout.
AI_JOB_POLL_INTERVAL_SECONDS = 2.0
AI_JOB_MAX_WAIT_SECONDS = 45.0


def _row_to_study_out(s: Study, p: Patient, r: Optional[Report]) -> StudyOut:
    return StudyOut(
        id=s.id,
        patient=PatientSummary(
            id=p.id,
            visible_id=p.visible_id,
            name=decrypt(p.name_encrypted) or "",
        ),
        modality=s.modality,
        status=s.status,
        urgency=s.urgency,
        primary_finding=r.primary_finding if r else None,
        confidence=r.confidence if r else None,
        study_datetime=s.study_datetime,
    )


@router.get("", response_model=StudyListResponse)
def list_studies(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    status_filter: Optional[StudyStatus] = Query(None, alias="status"),
    modality: Optional[Modality] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List studies visible to the current user.

    Shared-mode (or admin): all studies in the org.
    Per-doctor mode (non-admin): only studies assigned to the current user.
    """
    q = (
        select(Study, Patient, Report)
        .join(Patient, Study.patient_id == Patient.id)
        .outerjoin(Report, Report.study_id == Study.id)
    )

    if can_see_all_org_data(db, current):
        q = q.where(Study.org_id == current.org_id)
    else:
        q = q.where(Study.assigned_radiologist == current.id)

    if status_filter:
        q = q.where(Study.status == status_filter)
    if modality:
        q = q.where(Study.modality == modality)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(
        q.order_by(Study.study_datetime.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()

    items = [_row_to_study_out(s, p, r) for s, p, r in rows]
    return StudyListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=StudyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_study(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    request: Request,
    file: UploadFile = File(...),
    modality: Modality = Form(...),
    patient_id: Optional[uuid.UUID] = Form(None),
    mrn: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    age: Optional[int] = Form(None),
    sex: Optional[str] = Form(None),
    urgency: Urgency = Form(Urgency.routine),
    lang: str = Form("en"),
    report_type: str = Form("clinical"),
    region: Optional[str] = Form(None),
):
    """Upload an image, resolve or create the patient, persist a study, invoke the AI service, persist the report.

    For v1 this is synchronous. Move to a Celery task for MRI/PET later.
    """
    image_bytes = await file.read()
    image_sha = hashlib.sha256(image_bytes).hexdigest()
    ip = request.client.host if request.client else None

    # --- Resolve or create patient ---
    if patient_id:
        patient = db.get(Patient, patient_id)
        if not patient or patient.org_id != current.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    else:
        if not (mrn and name):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Provide patient_id, OR both mrn and name to create a new patient",
            )
        patient = db.query(Patient).filter(
            Patient.org_id == current.org_id, Patient.mrn == mrn,
        ).first()
        if not patient:
            patient = Patient(
                org_id=current.org_id,
                visible_id=next_visible_id(db, current.org_id),
                mrn=mrn,
                name_encrypted=encrypt(name),
                sex=sex,
                created_by=current.id,
            )
            db.add(patient)
            db.flush()
            # Auto-assign creating doctor as primary (used in per_doctor mode; harmless in shared mode)
            db.add(PatientAssignment(
                patient_id=patient.id,
                doctor_user_id=current.id,
                assigned_by=current.id,
                assignment_type="primary",
            ))

    # --- Upload source image to storage ---
    storage = get_storage()
    ext = (file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "bin").lower()
    source_key = f"org_{current.org_id}/patient_{patient.id}/source_{image_sha[:12]}.{ext}"
    storage.put(image_bytes, source_key, content_type=file.content_type)

    # --- Persist study row ---
    study = Study(
        org_id=current.org_id,
        patient_id=patient.id,
        modality=modality,
        status=StudyStatus.processing,
        urgency=urgency,
        ordered_by=current.id,
        assigned_radiologist=current.id,
        source_image_key=source_key,
        source_image_sha256=image_sha,
    )
    db.add(study)
    db.flush()

    audit(db, current, "study.create", "study", study.id, ip=ip,
          extra={"modality": modality.value, "patient_id": str(patient.id)})

    # --- Free-tier quota gate (Heal for All) ---------------------------------
    # Bill the scan now that we're about to consume AI compute. Verified mission
    # orgs get a soft limit (never blocked mid-care); others are hard-limited and
    # get a 402 to upsell. usage_counters is the single honest source of truth.
    org = db.get(Org, current.org_id)
    q = quota.check_and_reserve(db, org)
    if not q.ok:
        study.status = StudyStatus.failed
        db.commit()
        audit(db, current, "study.quota_blocked", "study", study.id, success=False, ip=ip,
              extra={"used": q.used, "quota": q.quota})
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={"reason": "monthly_quota_exceeded", "used": q.used, "quota": q.quota},
        )
    # q.warn (>=80%) and q.over (verified org past cap) are available if you want
    # to surface a banner in the response later.
    # -------------------------------------------------------------------------

    # --- Invoke AI service ---
    try:
        meta = {"name": name, "age": age, "sex": sex, "lang": lang, "report_type": report_type, "region": region}
        prediction = analyze_image(image_bytes, file.filename or "upload", modality.value, meta)
    except AIServiceError as e:
        study.status = StudyStatus.failed
        db.commit()
        audit(db, current, "study.ai_failed", "study", study.id, success=False, ip=ip,
              extra={"error": str(e)})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"AI service failed: {e}")

    # --- Async job path (CT Chest, MRI, PET, Ultrasound) ---
    # analyze_image returns {"async": true, "job_id": ...} for these; without
    # polling the study would be saved with no PDF and report.pdf would 404.
    if prediction.get("async") and prediction.get("job_id"):
        job_id = str(prediction["job_id"])
        deadline = time.monotonic() + AI_JOB_MAX_WAIT_SECONDS
        completed = False
        while time.monotonic() < deadline:
            time.sleep(AI_JOB_POLL_INTERVAL_SECONDS)
            try:
                job = poll_job(job_id)
            except AIServiceError as e:
                study.status = StudyStatus.failed
                db.commit()
                audit(db, current, "study.ai_failed", "study", study.id, success=False, ip=ip,
                      extra={"error": f"job poll failed: {e}", "job_id": job_id})
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"AI service failed: {e}")

            job_status = str(job.get("status") or "").lower()
            if job_status == "complete":
                # Merge rather than replace: keep any fields the initial
                # response carried that the job payload omits.
                merged = dict(prediction)
                merged.update(job)
                prediction = merged
                completed = True
                break
            if job_status == "error":
                study.status = StudyStatus.failed
                db.commit()
                audit(db, current, "study.ai_failed", "study", study.id, success=False, ip=ip,
                      extra={"error": str(job.get("error") or "job failed"), "job_id": job_id})
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    f"AI analysis failed: {job.get('error') or 'unknown error'}",
                )

        if not completed:
            study.status = StudyStatus.failed
            db.commit()
            audit(db, current, "study.ai_timeout", "study", study.id, success=False, ip=ip,
                  extra={"job_id": job_id, "waited_seconds": AI_JOB_MAX_WAIT_SECONDS})
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                "AI analysis is taking longer than expected. Please try again.",
            )

    # --- Re-store the PDF under portal-controlled key ---
    pdf_key = ""
    pdf_url = prediction.get("pdf_url") or prediction.get("report_url")
    if pdf_url:
        try:
            pdf_bytes = fetch_pdf(pdf_url)
            pdf_key = f"org_{current.org_id}/patient_{patient.id}/study_{study.id}/report.pdf"
            storage.put(pdf_bytes, pdf_key, content_type="application/pdf")
        except AIServiceError:
            pdf_key = ""  # Prediction JSON still useful even without the PDF

    # --- Persist report ---
    pred_payload = prediction.get("prediction", prediction)
    primary_finding = str(
        pred_payload.get("label")
        or pred_payload.get("prediction")
        or pred_payload.get("assessment")
        or "Unknown"
    )
    confidence = float(pred_payload.get("confidence") or 0.0)
    urgency_str = (pred_payload.get("urgency") or pred_payload.get("urgency_level") or "").upper()

    report = Report(
        study_id=study.id,
        prediction_json=prediction,
        primary_finding=primary_finding,
        confidence=confidence,
        pdf_key=pdf_key,
        ai_service_version=str(
            prediction.get("model_version") or prediction.get("version") or "unknown"
        ),
    )
    db.add(report)

    # --- Status + urgency transition ---
    if urgency_str == "STAT":
        study.urgency = Urgency.stat
        study.status = StudyStatus.critical
    elif urgency_str == "URGENT":
        study.urgency = Urgency.urgent
        study.status = StudyStatus.critical
    else:
        study.status = StudyStatus.awaiting_review

    db.commit()
    db.refresh(study)

    return StudyCreateResponse(
        study_id=study.id,
        status=study.status,
        primary_finding=primary_finding,
        confidence=confidence,
    )


@router.get("/{study_id}", response_model=StudyOut)
def get_study(
    study_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    row = db.execute(
        select(Study, Patient, Report)
        .join(Patient, Study.patient_id == Patient.id)
        .outerjoin(Report, Report.study_id == Study.id)
        .where(Study.id == study_id, Study.org_id == current.org_id)
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Study not found")
    s, p, r = row
    if not can_see_all_org_data(db, current) and s.assigned_radiologist != current.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorized for this study")
    return _row_to_study_out(s, p, r)


@router.get("/{study_id}/report.pdf")
def get_report_pdf(
    study_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    """Stream the PDF (local storage) or 302 to a signed URL (cloud). Audit every read."""
    row = db.execute(
        select(Study, Report)
        .outerjoin(Report, Report.study_id == Study.id)
        .where(Study.id == study_id, Study.org_id == current.org_id)
    ).first()
    if not row or not row[1] or not row[1].pdf_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report PDF not available")
    s, r = row
    if not can_see_all_org_data(db, current) and s.assigned_radiologist != current.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorized for this report")

    audit(db, current, "report.read", "report", r.id,
          ip=request.client.host if request.client else None)

    storage = get_storage()

    # Local storage: stream the file inline. Browsers cannot open file:// URLs
    # from an http(s) origin, so signed_url() (which returns file://) is useless
    # in the browser. Stream directly instead.
    if isinstance(storage, LocalStorage):
        path = storage._path(r.pdf_key)
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="report.pdf"'},
        )

    # Cloud storage (firebase/s3): 302 to a short-lived signed URL.
    # PATCH:pdf-stream v1 — was RedirectResponse(signed_url). Browsers can't
    # follow a 302 into Firebase signed URLs from a CORS fetch (Firebase
    # doesn't send Access-Control-Allow-Origin headers). We stream the bytes
    # back through this domain so CORS is satisfied by our own middleware.
    try:
        pdf_bytes = storage.get(r.pdf_key)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Failed to fetch report from storage: {exc}",
        )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="report.pdf"',
            "Cache-Control": "private, max-age=300",
        },
    )
@router.post("/{study_id}/review")
def review_study(
    study_id: uuid.UUID,
    body: ReviewRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
):
    study = db.get(Study, study_id)
    if not study or study.org_id != current.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Study not found")
    if not can_see_all_org_data(db, current) and study.assigned_radiologist != current.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorized for this study")

    report = db.query(Report).filter(Report.study_id == study.id).first()
    if not report:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not generated yet")

    report.reviewed_by = current.id
    report.reviewed_at = datetime.now(timezone.utc)
    report.clinician_notes = body.clinician_notes
    study.status = StudyStatus.completed
    db.commit()

    audit(db, current, "study.review", "study", study.id,
          ip=request.client.host if request.client else None)

    return {"ok": True, "status": study.status.value}
