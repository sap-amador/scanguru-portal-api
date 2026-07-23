"""HTTP client to the AI inference backend (your existing Railway service).

The Railway backend exposes a single unified endpoint for all modalities:

    POST /predict_with_report?lang={lang}
    multipart/form-data:
      - file       (required) — the image bytes
      - modality   (required) — CXR | CT_BRAIN | DENTAL | MSK | ...
      - name, age, sex, location  — patient context (strings)
      - consent    — 'true'
      - region     — optional, MSK-style modality region selector

Response is JSON. Synchronous modalities (CXR, Dental, MSK, Mammo) return the
full prediction inline:
    {
      "prediction": {label, confidence, urgency, top_findings, probabilities, ...},
      "pdf_url": "...", "report_id": "...",
      "async": false
    }

Slow modalities (MRI, PET, CT Chest) may return:
    { "async": true, "job_id": "..." }
The caller then polls GET /job/{job_id} until status == "complete".
"""
import httpx

from app.config import settings


class AIServiceError(Exception):
    pass


def analyze_image(
    image_bytes: bytes,
    filename: str,
    modality: str,
    meta: dict | None = None,
) -> dict:
    """Synchronous call to /predict_with_report. Returns the AI response JSON.

    If the response is async (job-based), the dict will contain
    ``async: true`` and ``job_id``; the caller is responsible for polling.
    """
    meta = meta or {}
    lang = str(meta.get("lang") or "en")
    # Whitelist rather than pass through: keeps the value safe to interpolate
    # into the query string and guards against a typo silently producing a
    # different report than the clinician asked for.
    report_type = str(meta.get("report_type") or "clinical").strip().lower()
    if report_type not in ("clinical", "research", "patient"):
        report_type = "clinical"

    files = {"file": (filename, image_bytes)}
    data: dict[str, str] = {
        "modality": modality,
        "consent": "true",
        "name": str(meta.get("name") or "Patient"),
        "age": str(meta.get("age") or "45"),
        "sex": str(meta.get("sex") or "Unknown"),
        "location": str(meta.get("location") or "Demo"),
        "report_type": report_type,
    }
    # `region` is used by MSK and a couple of other modalities. Pass through if given.
    if meta.get("region"):
        data["region"] = str(meta["region"])

    url = (
        f"{settings.ai_service_url.rstrip('/')}/predict_with_report"
        f"?lang={lang}&report_type={report_type}"
    )
    try:
        with httpx.Client(timeout=settings.ai_service_timeout_seconds) as client:
            r = client.post(url, files=files, data=data)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise AIServiceError(f"AI service call failed: {e}") from e


def fetch_pdf(pdf_url: str) -> bytes:
    """Pull a PDF the AI backend generated, so we can re-upload to portal storage with proper ACLs."""
    try:
        with httpx.Client(timeout=60) as client:
            r = client.get(pdf_url)
            r.raise_for_status()
            return r.content
    except httpx.HTTPError as e:
        raise AIServiceError(f"PDF fetch failed: {e}") from e


def poll_job(job_id: str) -> dict:
    """Poll a slow-modality job. Used when analyze_image returned async=True.

    Returns the same shape as analyze_image once status == 'complete'.
    """
    url = f"{settings.ai_service_url.rstrip('/')}/job/{job_id}"
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise AIServiceError(f"Job poll failed: {e}") from e
