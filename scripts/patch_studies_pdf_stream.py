#!/usr/bin/env python3
"""
Patch app/routers/studies.py:
- Update imports to include FileResponse and LocalStorage
- Replace the get_report_pdf() function so that:
    * Local storage  -> streams the PDF (FileResponse) — fixes the file:// bug
    * Cloud storage  -> 302 to signed URL (unchanged)

Run from your scanguru-portal repo root:

    python3 scripts/patch_studies_pdf_stream.py

It backs up studies.py to studies_backup_<timestamp>.py before editing.
"""
import os, re, shutil, sys
from datetime import datetime

TARGET = "app/routers/studies.py"

if not os.path.exists(TARGET):
    sys.exit(f"ERROR: {TARGET} not found. Run this from your scanguru-portal repo root.")

backup = f"app/routers/studies_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.py"
shutil.copy(TARGET, backup)
print(f"Backed up to {backup}")

with open(TARGET) as f:
    src = f.read()

# --- 1) Update imports ---
# fastapi.responses: ensure FileResponse is imported alongside RedirectResponse
src = re.sub(
    r"from fastapi\.responses import RedirectResponse",
    "from fastapi.responses import FileResponse, RedirectResponse",
    src,
)

# app.storage: ensure LocalStorage is imported alongside get_storage
src = re.sub(
    r"from app\.storage import get_storage",
    "from app.storage import get_storage, LocalStorage",
    src,
)

# --- 2) Replace the get_report_pdf function body ---
NEW_FN = '''@router.get("/{study_id}/report.pdf")
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
    url = storage.signed_url(r.pdf_key, ttl_seconds=900)
    return RedirectResponse(url=url, status_code=302)
'''

pattern = re.compile(
    r'@router\.get\("\/\{study_id\}\/report\.pdf"\).*?return RedirectResponse\(url=url, status_code=302\)\n',
    re.DOTALL,
)
if not pattern.search(src):
    sys.exit("ERROR: Could not find the get_report_pdf function in studies.py. "
             "Has the file been modified beyond what this patch expects? "
             f"The backup is at {backup}.")

src = pattern.sub(NEW_FN, src, count=1)

with open(TARGET, "w") as f:
    f.write(src)

print(f"Patched {TARGET}.")
print("Restart uvicorn (Ctrl-C then re-run it) so the change loads.")
