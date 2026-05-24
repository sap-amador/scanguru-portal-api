# ScanGuru Portal — Architecture Document

**Version:** 1.0
**Last Updated:** May 24, 2026
**Audience:** Developers, support team, future engineers, future Amador-in-6-months
**Purpose:** Single source of truth for what the system is, what's in it, and how the pieces fit

---

## 1. SYSTEM OVERVIEW

ScanGuru is a multilingual medical AI imaging SaaS that generates radiology reports across 14 modalities (CXR, CT, MRI, Mammography, Dental, Ultrasound, PET, etc.) in 83 languages.

The platform is split into **three independent services**, each in its own GitHub repository, each deployed independently. This separation is deliberate — the AI service evolves on model-release cadence (weeks), the portal evolves on feature cadence (days), and the marketing site evolves on copy cadence (whenever).

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          THE THREE SERVICES                              │
└──────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────┐       ┌─────────────────────────┐
   │  scanguru.net           │       │  Try-It-Yourself        │
   │  (Webflow marketing)    │       │  /try page              │
   │                         │       │  42-language demo       │
   │  - Homepage             │       │                         │
   │  - 9 modality pages     │       │  Public, no login       │
   │  - Pilot/About/Contact  │       │                         │
   └─────────────────────────┘       └─────────────────────────┘
                                                │
                                                ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  AI INFERENCE SERVICE                                            │
   │  Repo:     sap-amador/MedScan-AI                                 │
   │  Hosting:  Railway (adorable-simplicity-production)              │
   │  Branch:   probe-final                                           │
   │                                                                  │
   │  - 14 modality models (CXR, CT, MRI, etc.)                       │
   │  - PDF generators (4 pages, dual-language, signed)               │
   │  - 82 PDF translation files (pdf_translations/<lang>/*)          │
   │  - Image handlers (DICOM, PNG, JPG)                              │
   │  - Public endpoint: POST /predict_with_report                    │
   └──────────────────────────────────────────────────────────────────┘
                                ▲
                                │ HTTP (internal)
                                │
   ┌──────────────────────────────────────────────────────────────────┐
   │  PORTAL BACKEND                                                  │
   │  Repo:     sap-amador/scanguru-portal-api                        │
   │  Hosting:  Railway (scanguru-portal-api-production)              │
   │  Branch:   main                                                  │
   │                                                                  │
   │  - FastAPI app                                                   │
   │  - Postgres database (10 tables)                                 │
   │  - JWT auth + bcrypt + TOTP scaffold                             │
   │  - Multi-tenant (org_id isolation)                               │
   │  - PHI encryption (Fernet)                                       │
   │  - Storage adapter → Firebase Cloud Storage                      │
   │  - Audit logging (HIPAA)                                         │
   └──────────────────────────────────────────────────────────────────┘
                                ▲
                                │ HTTP (Authorization: Bearer <JWT>)
                                │
   ┌──────────────────────────────────────────────────────────────────┐
   │  PORTAL FRONTEND                                                 │
   │  Repo:     sap-amador/ScanGuru-web                               │
   │  Hosting:  GitHub Pages                                          │
   │  URL:      https://sap-amador.github.io/ScanGuru-web/            │
   │                                                                  │
   │  - 4 static HTML files                                           │
   │  - Vanilla JS (no build step)                                    │
   │  - Fetch + JWT bearer pattern                                    │
   │  - Designed for: portal.scanguru.net (when DNS goes live)        │
   └──────────────────────────────────────────────────────────────────┘
```

---

## 2. PRODUCTION URLS

| Service | URL | Hosting |
|---|---|---|
| Marketing site | `https://scanguru.net` | Webflow |
| Try-It-Yourself demo | `https://scanguru.net/try` | Webflow + embed |
| AI inference service | `https://adorable-simplicity-production.up.railway.app` | Railway |
| Portal backend (API) | `https://scanguru-portal-api-production.up.railway.app` | Railway |
| Portal frontend (UI) | `https://sap-amador.github.io/ScanGuru-web/` | GitHub Pages |
| Portal login page | `https://sap-amador.github.io/ScanGuru-web/portal-login.html` | GitHub Pages |

**Future custom domains (not yet configured):**
- `api.scanguru.net` → portal backend
- `portal.scanguru.net` → portal frontend

---

## 3. REPOSITORIES

### 3.1 `sap-amador/MedScan-AI` (AI Inference)

**Purpose:** Image analysis, PDF generation, multilingual reports.

**Key directories:**
```
MedScan-AI/
├── models/
│   └── handlers/                    Per-modality handlers
│       ├── cxr_50path.py            Chest X-Ray ensemble
│       ├── ct_brain_comprehensive.py
│       ├── dental.py                Three-variant architecture
│       ├── msk_comprehensive.py     With region picker logic
│       └── ...                      (10 more)
├── pdf_translations/                 82 language directories
│   ├── en/common.json
│   ├── es/common.json
│   ├── zh/common.json
│   └── ...                          (79 more)
├── pdf_report_*.py                  Per-modality PDF generators
├── pdf_report_ct_brain_v4.py        (1,431 lines, Qure.ai parity)
├── pdf_cxr_comprehensive.py
├── pdf_msk_comprehensive.py
├── pdf_factory_master.py            Routes report_type → generator
├── ct_brain_measurements.py         Marshall grade, ICH volume, midline shift
└── requirements.txt
```

**Deployment:** Auto-deploys on push to `probe-final` branch.

**This document does NOT cover the AI service in detail** — it has its own changelog and deployment process. See `MedScan-AI/README.md` and prior session transcripts.

### 3.2 `sap-amador/scanguru-portal-api` (Portal Backend)

**Purpose:** All stateful portal data + business logic.

**Complete directory structure:**
```
scanguru-portal-api/
├── app/
│   ├── __init__.py
│   ├── main.py                      FastAPI entrypoint, CORS, router mounts
│   ├── config.py                    Pydantic Settings (reads env vars)
│   ├── database.py                  SQLAlchemy engine, get_db dependency
│   ├── models.py                    10 SQLAlchemy 2.0 ORM tables
│   ├── schemas.py                   Pydantic request/response shapes
│   ├── auth.py                      JWT, bcrypt password hash, TOTP scaffold
│   ├── access.py                    RBAC + per-doctor patient assignment
│   ├── crypto.py                    Fernet encrypt/decrypt for PHI
│   ├── audit.py                     Audit log writer
│   ├── storage.py                   Storage adapter (local/firebase/s3)
│   ├── ai_client.py                 HTTP client → AI service
│   ├── utils/
│   │   ├── __init__.py
│   │   └── visible_id.py            PT-YYYY-NNNN atomic counter
│   └── routers/
│       ├── __init__.py
│       ├── auth.py                  POST /login, GET /me
│       ├── dashboard.py             GET /stats
│       ├── studies.py               Studies CRUD + PDF streaming
│       └── patients.py              Patients list/search/create/detail/timeline
│
├── alembic/                          Database migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 66b4e65ebc4e_initial_schema.py
│       └── 11c53e890958_add_org_access_mode_for_shared_clinic_.py
├── alembic.ini
│
├── scripts/
│   └── seed.py                       Creates initial org + admin user
│
├── docs/
│   ├── DEVELOPER_DEPLOYMENT_GUIDE.md
│   ├── PORTAL_ARCHITECTURE.md        ← this document
│   └── TROUBLESHOOTING.md            ← companion runbook
│
├── requirements.txt                  Pinned versions
├── Dockerfile                        Multi-stage build for Railway
├── docker-compose.yml                Local dev (Postgres + portal)
├── .env.example                      Required env vars (no secrets)
├── .gitignore                        Excludes .env, storage/, venv/
├── .dockerignore                     Excludes .env from container builds
└── README.md
```

### 3.3 `sap-amador/ScanGuru-web` (Portal Frontend)

**Purpose:** Doctor-facing UI. Pure static HTML, no build step.

```
ScanGuru-web/
├── portal-login.html                JWT login form
├── dashboard-main.html              Main dashboard (stat tiles, table, upload modal)
├── patient-detail.html              Per-patient profile with study timeline
├── patients.html                    Patient list with search + Add Patient
├── assets/
│   └── scanguru-mark.png            Logo (used in all pages)
└── README.md
```

**Configuration:** Each HTML file has `const API_BASE = 'https://scanguru-portal-api-production.up.railway.app/api/v1';` near the top of its `<script>` block.

---

## 4. DATABASE SCHEMA

**Engine:** PostgreSQL 18 (Railway-managed)
**ORM:** SQLAlchemy 2.0
**Migrations:** Alembic
**Total tables:** 11 (10 application + `alembic_version`)

### 4.1 Table inventory

| Table | Purpose | Row volume estimate |
|---|---|---|
| `orgs` | Clinics/hospitals (tenants) | 1 per pilot clinic |
| `users` | Doctors, admins, technologists | 5-20 per org |
| `patients` | Patient records (PHI encrypted) | Hundreds-thousands per org |
| `patient_assignments` | Per-doctor patient access | Many per doctor |
| `patient_insurance` | Insurance policies (table exists, no UI yet) | 1-3 per patient |
| `studies` | Imaging studies (one per scan) | Multiple per patient |
| `reports` | AI-generated reports | One per study |
| `clinical_notes` | Doctor notes (table exists, no UI yet) | Many per patient |
| `audit_log` | HIPAA audit trail | Many per request |
| `visible_id_counters` | Per-org annual sequence for PT-YYYY-NNNN | 1 per org per year |
| `alembic_version` | Migration tracking | 1 row |

### 4.2 Key relationships

```
orgs ──┬──< users ──┬──< patient_assignments >── patients ──┬──< studies ──< reports
       │            │                                       │
       │            └──< audit_log                           ├──< patient_insurance
       │                                                    │
       └──< visible_id_counters                             └──< clinical_notes
```

### 4.3 PHI encryption

The following columns store **Fernet-encrypted bytes**, not plain text:

| Table | Encrypted columns |
|---|---|
| `patients` | `name_encrypted`, `phone_encrypted`, `email_encrypted`, `address_encrypted` |
| `patient_insurance` | `policy_number_encrypted`, `subscriber_name_encrypted` |

**Access pattern:** `app/crypto.py` exposes `encrypt(str) -> bytes` and `decrypt(bytes) -> str`. Routers decrypt on read, encrypt on write. **Never log decrypted PHI.**

**Encryption key:** `PHI_ENCRYPTION_KEY` env var (Fernet base64). **Losing this key = all encrypted patient data is permanently unreadable.** Stored in:
- Amador's password manager
- Railway env vars (encrypted at rest)
- Nowhere else

### 4.4 Multi-tenant isolation

Every query against `patients`, `studies`, `reports`, `audit_log`, `clinical_notes`, `patient_insurance` MUST filter by `org_id`. The `get_current_user` dependency provides `current.org_id` from the JWT claim.

**`org.access_mode`** controls per-doctor visibility:
- `shared` (default for small clinics) → all org doctors see all patients
- `per_doctor` → doctors only see patients assigned via `patient_assignments` table

### 4.5 Human-readable IDs

`patients.visible_id` is `PT-YYYY-NNNN` (e.g. `PT-2026-0001`). Generated by `app/utils/visible_id.py::next_visible_id()` using `visible_id_counters` table for atomicity per org per year. UUIDs are the actual PKs; `visible_id` is what doctors see.

---

## 5. API ENDPOINTS

All routes under `/api/v1/`. All require `Authorization: Bearer <JWT>` except `/health` and `/api/v1/auth/login`.

| Method | Path | Purpose | Roles allowed |
|---|---|---|---|
| `GET` | `/health` | Liveness check | (public) |
| `POST` | `/api/v1/auth/login` | Email + password → JWT | (public) |
| `GET` | `/api/v1/auth/me` | Current user profile | any auth |
| `GET` | `/api/v1/dashboard/stats` | 5 stat-card counts | any auth |
| `GET` | `/api/v1/studies` | Paginated list, filterable | any auth (scoped) |
| `POST` | `/api/v1/studies` | Multipart upload + AI call | admin/radiologist/technologist |
| `GET` | `/api/v1/studies/{id}` | Study detail | any auth (scoped) |
| `GET` | `/api/v1/studies/{id}/report.pdf` | PDF download/redirect | any auth (scoped) |
| `POST` | `/api/v1/studies/{id}/review` | Mark reviewed + notes | radiologist |
| `GET` | `/api/v1/patients` | List + search + paginate | any auth |
| `POST` | `/api/v1/patients` | Create patient | admin/radiologist/technologist |
| `GET` | `/api/v1/patients/{id}` | Patient detail | any auth (scoped) |
| `GET` | `/api/v1/patients/{id}/timeline` | Patient + their studies | any auth (scoped) |

**Filters on `GET /studies`:**
- `?status=` — queued / processing / awaiting_review / completed / critical / failed
- `?modality=` — cxr / ct_brain / mri_spine / mammography / dental / ...
- `?page=` and `?page_size=` — 1-100

**JWT claims:**
- `sub` — user UUID
- `org` — org UUID
- `role` — admin / radiologist / technologist
- `exp` — 30-day expiry

---

## 6. INFRASTRUCTURE

### 6.1 Railway

**Project:** `wonderful-friendship`
**Workspace:** sap-amador's Projects
**Region:** us-west2

**Services in the project:**
1. **`scanguru-portal-api`** — backend, deployed from `sap-amador/scanguru-portal-api` (main branch)
2. **`Postgres`** — managed Postgres 18 with SSL
3. **Note:** A leftover `postgres-volume` (1.1GB) exists from a deleted prior Postgres service — does NOT affect operation, but can be cleaned up later

**Backend deployment:**
- Trigger: push to `main` branch on GitHub
- Builder: Docker (uses repo's `Dockerfile`)
- Runtime: Gunicorn + Uvicorn workers, port 8000
- Workers: 2 (configurable in Dockerfile CMD)
- Healthcheck: `/health` (returns 200 + JSON)

**Postgres:**
- Image: `ghcr.io/railwayapp-templates/postgres-ssl:18`
- Volume: `postgres-volume-_7Xn` (50GB capacity, auto-expanding)
- Backups: **NOT YET ENABLED** — enable via Postgres service → Settings → Backups
- Connection: `postgres.railway.internal:5432` (private, only reachable from within Railway project)
- Public access: also available via `kodama.proxy.rlwy.net:<port>` (used for migrations from laptop)

### 6.2 Environment variables (backend service)

Set on Railway → `scanguru-portal-api` service → Variables tab:

| Variable | Purpose | Example/format |
|---|---|---|
| `JWT_SECRET` | JWT signing | 64-char hex |
| `PHI_ENCRYPTION_KEY` | PHI Fernet encryption | 44-char base64 ending `=` |
| `DATABASE_URL` | Postgres connection | **Reference variable** (see below) |
| `AI_SERVICE_URL` | AI service base URL | `https://adorable-simplicity-production.up.railway.app` |
| `STORAGE_BACKEND` | Storage adapter mode | `firebase` |
| `FIREBASE_CREDENTIALS_B64` | Firebase service account JSON | 3188-char base64 |
| `FIREBASE_STORAGE_BUCKET` | Firebase bucket name | `scanguru-ai-903a4.firebasestorage.app` |
| `CORS_ALLOW_ORIGINS` | Allowed frontend origins (comma-separated) | `https://sap-amador.github.io,https://scanguru.net,http://localhost:3000` |
| `ENV` | Runtime mode | `prod` (disables `/docs`) |

**DATABASE_URL value (exact string):**
```
postgresql+psycopg2://${{Postgres.POSTGRES_USER}}:${{Postgres.POSTGRES_PASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.POSTGRES_DB}}
```

This is a **Railway reference variable** — the `${{Service.VAR}}` syntax resolves at deploy time. When Postgres credentials rotate, this auto-updates without manual intervention. **Never replace this with a hardcoded URL.** (See Troubleshooting doc for why — it's the source of half of deployment day's pain.)

### 6.3 Firebase

**Project:** `scanguru-ai-903a4`
**Project name:** ScanGuru-AI
**Storage bucket:** `scanguru-ai-903a4.firebasestorage.app` (newer format — NOT `.appspot.com`)
**Service account:** `firebase-adminsdk-fbsvc-8154d6b79a` (key stored at `~/.scanguru-secrets/` on Amador's Mac)

The same Firebase project is used by both the AI service (for source images) and the portal backend (for PDF reports).

### 6.4 GitHub Pages

Enabled on `sap-amador/ScanGuru-web`:
- Source: branch `main`, folder `/ (root)`
- URL: `https://sap-amador.github.io/ScanGuru-web/`
- Build: automatic on every push to main

---

## 7. AUTHENTICATION FLOW

```
   Doctor's browser                Portal backend              Postgres
        │                                │                        │
        │ POST /api/v1/auth/login        │                        │
        │ {email, password}              │                        │
        │───────────────────────────────▶│                        │
        │                                │ SELECT * FROM users    │
        │                                │ WHERE email = ?        │
        │                                │───────────────────────▶│
        │                                │◀───────────────────────│
        │                                │                        │
        │                                │ bcrypt.verify(password,│
        │                                │   user.password_hash)  │
        │                                │                        │
        │                                │ jwt.encode({           │
        │                                │   sub: user.id,        │
        │                                │   org: user.org_id,    │
        │                                │   role: user.role,     │
        │                                │   exp: now + 30 days   │
        │                                │ }, JWT_SECRET)         │
        │                                │                        │
        │ 200 {access_token, token_type} │                        │
        │◀───────────────────────────────│                        │
        │                                │                        │
        │ (browser stores in             │                        │
        │  sessionStorage)               │                        │
        │                                │                        │
        │ GET /api/v1/anything           │                        │
        │ Authorization: Bearer eyJ...   │                        │
        │───────────────────────────────▶│                        │
        │                                │ jwt.decode(token,      │
        │                                │   JWT_SECRET)          │
        │                                │ → current_user         │
        │                                │                        │
        │                                │ (query with            │
        │                                │  WHERE org_id =        │
        │                                │  current_user.org_id)  │
        │                                │───────────────────────▶│
```

**Session storage:** Frontend stores JWT in `sessionStorage` under key `scanguru_token`. Cleared on logout. Auto-redirected to login on 401.

---

## 8. UPLOAD/INFERENCE FLOW

The full path of a CXR upload from "+ New Analysis" button to PDF download:

```
   Doctor clicks "+ New Analysis", fills modal, clicks Analyze
        │
        ▼
   POST /api/v1/studies (multipart)
        │
        │  Form fields:
        │   - file: <binary>
        │   - modality: "cxr"
        │   - patient_name: "John Doe"
        │   - patient_mrn: "MRN-12345"
        │   - patient_sex: "M"
        │   - patient_age: 45
        │   - lang: "en"
        │   - report_type: "clinical"
        │
        ▼
   Portal backend
        │
        ├─→ Find or create patient (encrypt PHI)
        ├─→ Create studies row (status=queued)
        ├─→ Write audit_log row
        │
        ├─→ HTTP POST to AI_SERVICE_URL/predict_with_report
        │   (synchronous; can take 10-120s)
        │
        │   ┌──────────────────────────────────────┐
        │   │ AI INFERENCE SERVICE                 │
        │   │                                      │
        │   │ - Load model for modality            │
        │   │ - Run inference                      │
        │   │ - Generate PDF (4 pages, dual-lang)  │
        │   │ - Upload PDF to Firebase             │
        │   │ - Return JSON + PDF URL              │
        │   └──────────────────────────────────────┘
        │
        ├─→ Receive: {prediction, pdf_url, confidence, ...}
        ├─→ Fetch PDF, re-upload to portal storage
        │   (org_<uuid>/patient_<uuid>/study_<uuid>/report.pdf)
        ├─→ Create reports row
        ├─→ Update studies row (status=awaiting_review or critical)
        ├─→ Write audit_log row
        │
        ▼
   Response: {study_id, report_id, prediction, pdf_url: <signed>}
        │
        ▼
   Frontend updates dashboard table, shows new row

   Doctor clicks "View Report"
        │
        ▼
   GET /api/v1/studies/{id}/report.pdf
        │
        ▼
   Backend generates 15-min signed URL → 302 redirect
        │
        ▼
   Browser downloads PDF directly from Firebase
```

**Known limitation:** MRI and PET inference exceeds Railway's request timeout (~60s). For pilot, use CXR/Dental/MSK/CT Brain only. Async job queue (Celery + Redis) is on the post-pilot roadmap.

---

## 9. ROLES AND PERMISSIONS

Three roles, defined in `app/models.py::UserRole`:

| Role | Can see | Can create | Can review |
|---|---|---|---|
| `admin` | All org data | Patients, studies, users | Yes |
| `radiologist` | Assigned patients (or all if `shared` mode) | Patients, studies | Yes |
| `technologist` | All org studies | Patients, studies (no review) | No |

**Patient assignment** (only enforced when `org.access_mode = 'per_doctor'`):
- Admin assigns radiologists to patients via `patient_assignments` table
- Without an active assignment row, a radiologist gets 403 on patient detail/timeline
- Admins bypass this check always

**The seeded admin** (`admin@scanguru.net`) has `role=admin` and sees everything.

---

## 10. STATE TRANSITIONS

`Study.status` and `Study.urgency` are the two state machines that drive every dashboard count.

### 10.1 Study status

```
   queued  ──┬─→  processing  ──┬─→  awaiting_review  ──→  completed
             │                  │
             │                  └─→  critical  ──→  completed
             │
             └─→  failed (AI error / timeout)
```

**Dashboard tile mapping:**
- `In Progress` = `status IN (queued, processing)`
- `Pending Review` = `status = awaiting_review`
- `Urgent` = `urgency IN (urgent, stat)` (independent of status)
- `Completed` = `status = completed`
- `Critical` (sidebar count) = `status = critical`

### 10.2 Urgency

`Study.urgency` is computed by AI prediction at inference time:
- `routine` — default
- `urgent` — needs attention within hours
- `stat` — needs attention immediately

Urgency does NOT change with status — a critical finding stays critical even after review.

---

## 11. SECRETS INVENTORY

All production secrets, where they live, and what they do:

| Secret | Location | Used for | Rotation impact |
|---|---|---|---|
| `JWT_SECRET` | Password manager + Railway env | JWT signing | Logs out all users; safe to rotate anytime |
| `PHI_ENCRYPTION_KEY` | Password manager + Railway env | PHI encryption at rest | **Lose this = lose all PHI permanently.** Never rotate without re-encrypting first |
| `POSTGRES_PASSWORD` | Auto-managed by Railway | Database access | Auto-updates via reference variable on backend |
| Firebase service account JSON | `~/.scanguru-secrets/` + Railway env (base64) | Firebase storage access | Can regenerate in Firebase Console; invalidates old key |
| `admin@scanguru.net` password | Password manager | Initial admin login | Change anytime via `/api/v1/auth/change-password` |

**Password manager entry:** "ScanGuru Portal — Production Secrets" (contains all 5 above)

**NEVER:**
- Commit `.env` to git (gitignored)
- Paste secrets in chat, email, Slack, tickets
- Share secrets via screenshots
- Hardcode `DATABASE_URL` with embedded password (use reference variable)

---

## 12. THE 14 SUPPORTED MODALITIES

Each modality is independently handled by a dedicated AI model. Modality identifiers used in API calls:

| Modality | API value | Region picker? | Approx inference time |
|---|---|---|---|
| Chest X-Ray | `cxr` | No | 10-15s |
| CT Brain | `ct_brain` | No | 20-30s |
| CT Chest | `ct_chest` | No | 20-30s |
| MSK X-Ray | `msk` | **Yes** (9 regions) | 15-20s |
| Mammography | `mammography` | No | 15-25s |
| MRI Spine | `mri_spine` | No | 60-120s ⚠️ |
| MRI Knee | `mri_knee` | No | 60-120s ⚠️ |
| MRI Prostate | `mri_prostate` | No | 60-120s ⚠️ |
| MRI Breast | `mri_breast` | No | 60-120s ⚠️ |
| MRI Cardiac | `mri_cardiac` | No | 60-120s ⚠️ |
| MRI Shoulder | `mri_shoulder` | No | 60-120s ⚠️ |
| Dental | `dental` | No (3 report variants) | 15-25s |
| Ultrasound | `ultrasound` | No | 15-20s |
| PET | `pet` | No | 90-180s ⚠️ |

⚠️ **Modalities marked with warning currently exceed Railway request timeout.** For pilot, use the fast modalities only. Async job queue is on the post-pilot roadmap.

**MSK region picker** values (the 9 anatomical regions):
- Hand/Wrist
- Finger
- Forearm
- Elbow
- Shoulder
- Hip/Pelvis/Femur
- Knee
- Ankle/Foot
- Spine

**Dental three-variant architecture:** One model prediction → three different PDFs depending on `report_type` form field:
- `clinical` → 4-page dentist-facing
- `research` → 6-7 page researcher with appendix
- `patient` → 2-page traffic-light format

---

## 13. STUBBED FEATURES (NOT YET IMPLEMENTED)

These appear in the UI as "Coming Soon" placeholders. Schema may exist; routers/UI don't. Listed here so you don't think they're broken.

| Feature | Schema exists | Router exists | UI exists | Effort to ship |
|---|---|---|---|---|
| Analytics page | No | No | Sidebar stub only | 8-12 hours |
| Physicians page | No | No | Sidebar stub only | 4-6 hours |
| Preferences page | No | No | Sidebar stub only | 4-6 hours |
| Integrations page | No | No | Sidebar stub only | Variable |
| API Access page | Schema for `api_keys`, `api_usage` | No | Sidebar stub only | 8-12 hours |
| Insurance tab | `patient_insurance` table | No | Tab placeholder | 6-8 hours |
| Clinical Notes tab | `clinical_notes` table | No | Tab placeholder | 6-8 hours |
| Batch Analysis | No | No | Quick Actions stub | Variable |
| Export Reports | No | No | Quick Actions stub | 4 hours |
| Edit Patient | Backend supports | No (only POST) | None | 2-3 hours |
| Password Reset | No | No | None | 4 hours (+ SMTP) |
| 2FA enforcement | TOTP scaffolded in `app/auth.py` | Not required | None | 1-2 hours |
| Async job queue | No | No | None | 8-12 hours |
| Frontend i18n | No | N/A | English only | 6-8 hours + translator effort |

---

## 14. KEY DESIGN DECISIONS

These choices were made deliberately. Don't change them without understanding the tradeoff.

**14.1 Two backends, not one.** The AI inference service and the portal backend are separate. Different release cadences, different secrets, different concerns. Merging them would create deployment coupling.

**14.2 Fernet symmetric encryption for PHI, not KMS.** Appropriate for v1 pilot. At GA scale, switch to envelope encryption with per-tenant DEKs managed by AWS KMS or GCP Secret Manager. The migration is straightforward but unnecessary now.

**14.3 `org_id` is the multi-tenant boundary.** Every query must filter by it. Cross-org data access is a bug, not a feature.

**14.4 PatientAssignment is optional.** Default `org.access_mode = 'shared'` means all org doctors see all org patients. This is what small clinics want. The `per_doctor` mode exists for larger orgs that need stricter access control.

**14.5 Synchronous AI calls (not async queue).** Acceptable for fast modalities (10-30s). Pilot accordingly. Async is post-pilot work.

**14.6 Static frontend on GitHub Pages.** No build step, no framework, no server. Vanilla JS + fetch. Easy to debug, easy to deploy, no version drift. The cost: every page reloads on navigation (no SPA routing). Acceptable for v1.

**14.7 JWT in sessionStorage, not cookies.** Simpler, no CSRF concerns, no SameSite gymnastics. The cost: XSS would leak the token. Mitigation: aggressive Content-Security-Policy headers (not yet configured).

**14.8 Reference variables for DATABASE_URL.** Lesson learned the hard way on deployment day. Manual copying of DB URLs leads to stale-password drift when Postgres credentials rotate. The `${{Postgres.POSTGRES_PASSWORD}}` reference syntax auto-updates.

**14.9 `.dockerignore` excludes `.env`.** Pydantic Settings reads `.env` if present in the container, which overrides Railway env vars. Excluding `.env` from the Docker build ensures Railway env vars win.

**14.10 Visible IDs are per-org annual sequences.** `PT-2026-0001`. Atomic via `visible_id_counters` table. Don't expose UUIDs to doctors.

---

## 15. COSTS (ESTIMATED MONTHLY)

For a pilot with 1-5 clinics, 50 doctors, 1000 studies/month:

| Service | Estimate | Notes |
|---|---|---|
| Railway (backend + Postgres) | $10-25/mo | Free tier is $5; portal adds ~$15-20 |
| Firebase Storage | $5-15/mo | Depends on volume; PDFs are small (~200KB each) |
| Firebase egress | $5-10/mo | Doctor downloads ~10 PDFs/day each |
| GitHub Pages | $0 | Free |
| Webflow (existing) | (separate) | Already paying |
| **Total** | **~$20-50/mo** | Scales sublinearly with users |

At 10x scale: $80-200/mo. At 100x: probably time to migrate to dedicated infra.

---

## 16. DEPENDENCIES TO MONITOR

Pinned in `requirements.txt`. Watch these for CVEs and breaking changes:

| Package | Current | Why it matters |
|---|---|---|
| `fastapi` | 0.115.4 | Core framework |
| `sqlalchemy` | 2.0.36 | ORM, 2.0 API |
| `psycopg2-binary` | 2.9.10 | Postgres driver |
| `alembic` | 1.13.3 | Migrations |
| `python-jose[cryptography]` | 3.3.0 | JWT signing |
| `passlib[bcrypt]` | 1.7.4 | Password hashing |
| `bcrypt` | 4.x | Cosmetic warning with passlib; consider pinning `<4.1` |
| `cryptography` | 43.0.3 | Fernet for PHI |
| `firebase-admin` | 6.6.0 | Firebase SDK |

---

## 17. CONTACT / OWNERSHIP

- **Product owner:** Amador (sap-amador on GitHub)
- **Backend / portal:** Amador
- **AI models / inference:** Amador + Senthil
- **Frontend (marketing):** Webflow contractor
- **Frontend (portal):** Amador (built with AI assistance, May 2026)
- **Infrastructure:** Amador (Railway, Firebase, GitHub)

**For support escalation:** see `TROUBLESHOOTING.md` in this docs folder.

---

## 18. CHANGELOG

| Date | Change | Author |
|---|---|---|
| 2026-05-24 | Initial portal deploy to production | Amador |
| 2026-05-24 | Portal frontend on GitHub Pages | Amador |
| 2026-05-22..24 | Portal backend built (FastAPI + Postgres + Firebase) | Amador (with AI pair) |
| 2026-05-20 | ScanGuru-web frontend HTML iteration | Amador |
| (earlier) | MedScan-AI service (existing) | Amador + Senthil |
| (earlier) | scanguru.net marketing site (Webflow) | Webflow contractor |
