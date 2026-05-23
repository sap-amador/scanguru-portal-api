# ScanGuru Portal API

FastAPI backend for the ScanGuru doctor-facing dashboard. Multi-tenant, RBAC, JWT auth, HIPAA-aware audit logging. Source of truth for patients, studies, and reports. Calls the AI inference service for predictions; persists artifacts to its own object storage with signed-URL access.

## Architecture in one paragraph

This service owns all stateful data: patients, doctor assignments, studies, reports, audit. The AI backend (your existing Railway service at `AI_SERVICE_URL`) stays stateless — this portal calls its `/predict_image_multiclass` endpoint, captures the prediction JSON + PDF, re-stores the PDF under a portal-controlled storage key, and exposes everything to the dashboard via short-lived signed URLs. PHI columns (name, phone, email, address, insurance) are encrypted at the app layer with Fernet so the database row is opaque to anyone with SQL access.

## Quick start

```bash
# 1. Copy and fill in env vars
cp .env.example .env
# Generate JWT_SECRET
openssl rand -hex 32
# Generate PHI_ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Paste both into .env

# 2. Install deps
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Start Postgres (via docker-compose, or use your own)
docker compose up -d postgres

# 4. Create the initial migration (one time) and apply
alembic revision --autogenerate -m "initial schema"
alembic upgrade head

# 5. Seed an admin user
python scripts/seed.py \
  --org "Demo Clinic" \
  --email admin@demo.test \
  --password "ChangeMe123!" \
  --name "Admin User"

# 6. Run
uvicorn app.main:app --reload --port 8000

# 7. Open the docs
open http://localhost:8000/docs
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Email + password (+ TOTP if enabled). Returns JWT. |
| `GET`  | `/api/v1/auth/me` | Current user profile. |
| `GET`  | `/api/v1/dashboard/stats` | Stat-card counts (critical / urgent / pending / routine / completed_today). |
| `GET`  | `/api/v1/studies` | Paginated study list with `?status=&modality=&page=&page_size=` filters. |
| `POST` | `/api/v1/studies` | Multipart upload — file, modality, patient info. Creates study, calls AI, persists report. |
| `GET`  | `/api/v1/studies/{id}` | Study detail. |
| `GET`  | `/api/v1/studies/{id}/report.pdf` | 302 to short-lived signed URL. |
| `POST` | `/api/v1/studies/{id}/review` | Mark study reviewed + record clinician notes. |
| `GET`  | `/api/v1/patients/{id}` | Patient profile (PHI decrypted in response). |
| `GET`  | `/api/v1/patients/{id}/timeline` | All studies for a patient, with summary counts. |

Radiologists see only studies where `assigned_radiologist = current_user`. Admins see org-wide. Every authenticated request that touches a study or report writes an `audit_log` row.

## Wiring the dashboard frontend

Replace the static data in `dashboard-main.html` and `patient-detail.html` with `fetch()` calls. Cleanest order:

1. **Login** — POST to `/api/v1/auth/login`, store the access token (sessionStorage is fine for v1).
2. **Stat cards** — single GET to `/api/v1/dashboard/stats`.
3. **Recent Scans table** — GET `/api/v1/studies?page=1&page_size=20`, render rows.
4. **Filter chips** — same endpoint with `modality=` and `status=`.
5. **Patient detail** — GET `/api/v1/patients/{id}/timeline`, drives the header card and Study Timeline.
6. **View Report** — link to `/api/v1/studies/{id}/report.pdf` (302 to the signed URL).

## What's deliberately not in v1

- **Async job queue** — the AI call is synchronous in `POST /studies`. Slow modalities (MRI, PET) should move to a Celery task with `GET /studies/{id}/status` polling. Wiring Celery + Redis is a one-day add.
- **Developer API tier** — Bearer API keys, rate limits, the `/v1/analyze` shape from `api-documentation.html`. That's a separate FastAPI service against the same Postgres, planned for phase 2.
- **API key tables** (`api_keys`, `api_usage`) — schema-ready but not in this scaffold's models. Add when phase 2 starts.
- **WebSocket push** for real-time stat-card updates. Polling every 30s is fine until you have hundreds of concurrent doctors.
- **Insurance + clinical notes routers** — tables exist; CRUD endpoints are a few hours each. Add as the Patient Detail tabs come online.

## Storage adapter

Set `STORAGE_BACKEND` in `.env`:

- `local` — files written under `./storage/`. Dev only.
- `firebase` — set `FIREBASE_CREDENTIALS_B64` (base64-encoded service account JSON) and `FIREBASE_STORAGE_BUCKET`.
- `s3` — set `S3_BUCKET` + `S3_ACCESS_KEY` + `S3_SECRET_KEY`. For MinIO on-prem, also set `S3_ENDPOINT_URL=https://minio.your-domain`.

Object keys follow the pattern `org_<uuid>/patient_<uuid>/study_<uuid>/<file>` so per-tenant deletion is `delete by prefix`.

## Common ops

```bash
# After model changes, regenerate migration
alembic revision --autogenerate -m "add insurance fields"
alembic upgrade head

# Reset local DB
docker compose down -v && docker compose up -d postgres
alembic upgrade head
python scripts/seed.py --org "..." --email ... --password ... --name ...

# Run tests (not included yet — add pytest + fixtures)
pytest -v

# Production
gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000 app.main:app
```

## Notes on PHI encryption

- PHI columns are `LargeBinary` and hold Fernet-encrypted bytes.
- `app/crypto.py` exposes `encrypt(str) -> bytes` and `decrypt(bytes) -> str`.
- Rotate the key by re-encrypting all rows with a versioned key prefix (not built in; add when first rotation hits).
- The key itself must come from a secrets manager in prod (AWS KMS, GCP Secret Manager, Doppler). Don't commit `.env`.
