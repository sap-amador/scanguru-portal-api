# ScanGuru — Architecture Diagrams

Visual reference. Pulled out separately so it can be shared, pasted in presentations, or printed without the full architecture doc.

---

## DIAGRAM 1 — THE WHOLE SYSTEM (BIRD'S EYE VIEW)

```
                                  PUBLIC INTERNET
   ┌─────────────────────────────────────────────────────────────────────┐
   │                                                                     │
   │   PATIENTS                  DOCTORS                  DEVELOPERS     │
   │      │                         │                          │         │
   │      │                         │                          │         │
   └──────┼─────────────────────────┼──────────────────────────┼─────────┘
          │                         │                          │
          ▼                         ▼                          ▼
   ┌──────────────┐         ┌──────────────┐          ┌───────────────┐
   │  scanguru.   │         │   portal.    │          │  api.scanguru │
   │     net      │         │  scanguru.   │          │     .net      │
   │              │         │     net      │          │   (future)    │
   │  Marketing   │         │              │          │               │
   │   Webflow    │         │  Doctor      │          │  Developer    │
   │              │         │  portal      │          │  API tier     │
   │  + /try demo │         │              │          │               │
   └──────────────┘         └──────┬───────┘          └───────┬───────┘
                                   │                          │
                                   │ HTTPS                    │
                                   │ JWT Bearer               │
                                   ▼                          ▼
                            ┌──────────────────────────────────────┐
                            │                                      │
                            │       PORTAL BACKEND (FastAPI)       │
                            │                                      │
                            │  ┌────────────┐     ┌────────────┐   │
                            │  │   Auth     │     │ Studies    │   │
                            │  │   /me      │     │ /upload    │   │
                            │  │   /login   │     │ /list      │   │
                            │  └────────────┘     │ /report.pdf│   │
                            │                     └─────┬──────┘   │
                            │  ┌────────────┐           │          │
                            │  │  Patients  │           │          │
                            │  │  /list     │           │          │
                            │  │  /create   │           │          │
                            │  │  /detail   │           │          │
                            │  │  /timeline │           │          │
                            │  └────────────┘           │          │
                            │                           │          │
                            │  Fernet PHI encryption    │          │
                            │  Multi-tenant by org_id   │          │
                            │  HIPAA audit logging      │          │
                            └──────┬────────────────────┼──────────┘
                                   │                    │
                          ┌────────┘                    └────────┐
                          ▼                                      ▼
                  ┌───────────────┐                  ┌────────────────────┐
                  │   POSTGRES    │                  │  AI INFERENCE      │
                  │               │                  │  (MedScan-AI)      │
                  │  10 tables    │                  │                    │
                  │  PHI encrypted│                  │  14 modalities     │
                  │  Audit log    │                  │  83 languages      │
                  │               │                  │  PDF gen           │
                  └───────────────┘                  └─────────┬──────────┘
                                                               │
                                                               ▼
                                                     ┌────────────────────┐
                                                     │ FIREBASE STORAGE   │
                                                     │                    │
                                                     │  scanguru-ai-      │
                                                     │  903a4.firebase    │
                                                     │  storage.app       │
                                                     │                    │
                                                     │  PDF reports       │
                                                     │  Signed URLs       │
                                                     └────────────────────┘
```

---

## DIAGRAM 2 — REPOSITORIES & DEPLOYMENTS

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │                          GITHUB.COM/SAP-AMADOR                      │
   └─────────────────────────────────────────────────────────────────────┘

   ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
   │  MedScan-AI        │    │ scanguru-portal-api│    │  ScanGuru-web      │
   │  (existing)        │    │ (new, this build)  │    │  (frontend)        │
   │                    │    │                    │    │                    │
   │  Branch: probe-fnl │    │  Branch: main      │    │  Branch: main      │
   │                    │    │                    │    │                    │
   │  - models/         │    │  - app/            │    │  - portal-login    │
   │  - pdf_translations│    │    - routers/      │    │  - dashboard-main  │
   │  - pdf_report_*.py │    │    - models.py     │    │  - patient-detail  │
   │  - 82 languages    │    │    - auth.py       │    │  - patients        │
   │                    │    │    - storage.py    │    │  - assets/         │
   │                    │    │  - alembic/        │    │                    │
   │                    │    │  - Dockerfile      │    │                    │
   └────────┬───────────┘    └─────────┬──────────┘    └────────┬───────────┘
            │                          │                        │
            │ auto-deploy              │ auto-deploy            │ auto-deploy
            │ on push                  │ on push                │ on push
            ▼                          ▼                        ▼
   ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
   │  RAILWAY           │    │  RAILWAY           │    │  GITHUB PAGES      │
   │                    │    │                    │    │                    │
   │  adorable-         │    │  scanguru-portal-  │    │  sap-amador.       │
   │  simplicity-       │    │  api-production    │    │  github.io/        │
   │  production        │    │                    │    │  ScanGuru-web      │
   │                    │    │  + Postgres        │    │                    │
   │                    │    │                    │    │                    │
   └────────────────────┘    └────────────────────┘    └────────────────────┘
                                       │
                                       │ uses
                                       ▼
                             ┌────────────────────┐
                             │ FIREBASE           │
                             │ scanguru-ai-903a4  │
                             │                    │
                             │ Cloud Storage      │
                             └────────────────────┘
```

---

## DIAGRAM 3 — DATABASE SCHEMA

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │                       SCANGURU PORTAL DATABASE                       │
   │                       PostgreSQL 18 (Railway)                        │
   └──────────────────────────────────────────────────────────────────────┘

   ┌─────────────┐
   │    orgs     │  Tenants (clinics, hospitals)
   │─────────────│
   │ id (UUID)PK │
   │ name        │
   │ access_mode │  ─── 'shared' or 'per_doctor'
   │ created_at  │
   └─────┬───────┘
         │
         │ org_id (FK)
         │ ────────────────────────────────────────────────────────────┐
         ▼                                                              │
   ┌─────────────────┐                                                  │
   │     users       │  Doctors, admins, technologists                  │
   │─────────────────│                                                  │
   │ id (UUID) PK    │ ────┐                                            │
   │ org_id (FK)     │     │ user.id                                    │
   │ email           │     │                                            │
   │ password_hash   │     │                                            │
   │ role (enum)     │     │                                            │
   │   admin         │     │                                            │
   │   radiologist   │     │                                            │
   │   technologist  │     │                                            │
   │ is_active       │     │                                            │
   │ totp_secret     │     │                                            │
   │ created_at      │     │                                            │
   └─────────────────┘     │                                            │
                           │                                            │
                           │                                            │
   ┌─────────────────┐     │     ┌────────────────────────┐             │
   │ patient_        │◀────┘     │      patients          │             │
   │ assignments     │           │────────────────────────│             │
   │─────────────────│           │ id (UUID) PK           │ ◀───────────┘
   │ id (UUID) PK    │  patient  │ org_id (FK)            │  org_id
   │ patient_id (FK) │ ─────────▶│ visible_id (PT-NNNN)   │
   │ doctor_user_id  │           │ mrn                    │
   │ assigned_by     │           │ name_encrypted [PHI]   │
   │ assignment_type │           │ dob                    │
   │ revoked_at      │           │ sex                    │
   └─────────────────┘           │ phone_encrypted [PHI]  │
                                 │ email_encrypted [PHI]  │
                                 │ address_encrypted [PHI]│
                                 │ created_at             │
                                 └─────┬──────────────┬───┘
                                       │              │
                                       │ patient_id   │ patient_id
                                       ▼              ▼
                              ┌────────────────┐  ┌──────────────────┐
                              │   studies      │  │ patient_insurance│
                              │────────────────│  │ ─────────────────│
                              │ id (UUID) PK   │  │ id (UUID) PK     │
                              │ patient_id     │  │ patient_id       │
                              │ modality       │  │ provider         │
                              │ status (enum)  │  │ policy_encrypted │
                              │ urgency (enum) │  │ effective_from   │
                              │ region (MSK)   │  │ is_primary       │
                              │ uploaded_by    │  └──────────────────┘
                              │ created_at     │  (no UI router yet)
                              └─────┬──────────┘
                                    │
                                    │ study_id
                                    ▼
                              ┌──────────────────┐    ┌──────────────────┐
                              │    reports       │    │  clinical_notes  │
                              │──────────────────│    │ ─────────────────│
                              │ id (UUID) PK     │    │ id (UUID) PK     │
                              │ study_id (FK)    │    │ patient_id       │
                              │ prediction (JSON)│    │ study_id (opt)   │
                              │ confidence       │    │ author_user_id   │
                              │ pdf_storage_key  │    │ body             │
                              │ created_at       │    │ created_at       │
                              └──────────────────┘    └──────────────────┘
                                                      (no UI router yet)


   ┌─────────────────────┐         ┌──────────────────────────┐
   │     audit_log       │         │  visible_id_counters     │
   │─────────────────────│         │ ─────────────────────────│
   │ id (UUID) PK        │         │ org_id (FK) PK           │
   │ org_id (FK)         │         │ year (int) PK            │
   │ user_id (FK)        │         │ next_value (int)         │
   │ action              │         │                          │
   │ resource_type       │         │  ─── used by             │
   │ resource_id         │         │      utils/visible_id.py │
   │ ip                  │         │      for PT-YYYY-NNNN    │
   │ created_at          │         └──────────────────────────┘
   └─────────────────────┘
```

---

## DIAGRAM 4 — A LOGIN REQUEST, END TO END

```
   BROWSER                    GITHUB PAGES              RAILWAY (BACKEND)        POSTGRES
   ───────                    ────────────              ─────────────────        ────────

   GET portal-login.html ────▶
                              │
                              │ (returns static HTML
                              │  + embedded JS)
                              │
                              ▼
   ◀────────────────────────── 200 OK, HTML

   User types email +
   password, clicks
   "Sign In"

   POST /api/v1/auth/login ──────────────────────────▶
   {                                                  │
     email:    "admin@scanguru.net",                  │ JWT_SECRET from env
     password: "******"                               │ DATABASE_URL via
   }                                                  │   reference variable
                                                      │
                                                      │ SELECT * FROM users
                                                      │ WHERE email = ?  ─────▶
                                                      │
                                                      ◀─────────── user row + hash
                                                      │
                                                      │ bcrypt.verify(
                                                      │   password,
                                                      │   user.password_hash
                                                      │ )
                                                      │
                                                      │ jwt.encode({
                                                      │   sub:  user.id,
                                                      │   org:  user.org_id,
                                                      │   role: user.role,
                                                      │   exp:  +30 days
                                                      │ }, JWT_SECRET)
                                                      │
   ◀────────────────────────── 200 OK
   {
     access_token: "eyJ...",
     token_type:   "bearer"
   }

   Frontend stores in
   sessionStorage,
   redirects to dashboard

   GET dashboard-main.html ──▶
                              │
   ◀────────────────────────── 200 OK, HTML+JS

   Frontend fires multiple API calls:

   GET /api/v1/auth/me ────────────────────────────▶
     Authorization: Bearer eyJ...                    │
                                                     │ Validate JWT
                                                     │ → current_user
                                                     │ SELECT FROM users ─▶
                                                     ◀───── user row
   ◀──────────────────── 200 OK {full_name, role}

   GET /api/v1/dashboard/stats ────────────────────▶
                                                     │ SELECT count(*) GROUP BY status
                                                     │ WHERE org_id = current_user.org_id ▶
                                                     ◀───── counts
   ◀──────────────────── 200 OK {critical, urgent, ...}

   GET /api/v1/studies?page=1&page_size=100 ───────▶
                                                     │ SELECT * FROM studies
                                                     │ WHERE org_id = ?
                                                     │ ORDER BY created_at DESC LIMIT 100 ▶
                                                     ◀───── 100 study rows
   ◀──────────────────── 200 OK {items: [...], total}

   Dashboard renders with
   real data
```

---

## DIAGRAM 5 — UPLOADING A SCAN

```
   BROWSER              PORTAL BACKEND           AI SERVICE        FIREBASE STORAGE
   ───────              ──────────────           ──────────        ────────────────

   User clicks
   "+ New Analysis"
   Fills modal:
     - File (CXR)
     - Modality: cxr
     - Patient: John Doe
     - MRN: MRN-12345
     - Sex: M, Age: 45
     - Lang: en
     - Report type: clinical
   Clicks Analyze

   POST /api/v1/studies ──▶
   multipart form         │
   - file                 │ Find or create patient
   - modality=cxr         │ (encrypt name, phone)
   - patient_name=...     │ INSERT INTO patients
   - mrn=...              │ INSERT INTO studies
                          │   status=queued
                          │ INSERT INTO audit_log
                          │
                          │ POST /predict_with_report ─────▶
                          │ (sync, ~10-30s for CXR)         │
                          │                                 │
                          │                                 │ Load CXR model
                          │                                 │ Run inference
                          │                                 │ Generate PDF
                          │                                 │
                          │                                 │ Upload PDF ───────▶
                          │                                 │                    │
                          │                                 │ ◀─────────────────── signed URL
                          │                                 │
                          │ ◀──── prediction JSON +         │
                          │       pdf_url + signed URL       │
                          │
                          │ HTTP GET pdf_url ──────────────────────────────────▶
                          │                                                     │
                          │ ◀───── PDF bytes ───────────────────────────────────
                          │
                          │ Upload to portal's own       ──▶ org_<>/patient_<>/
                          │  storage key                     study_<>/report.pdf
                          │
                          │ INSERT INTO reports
                          │ UPDATE studies SET status=awaiting_review
                          │ INSERT INTO audit_log
                          │
   ◀───── 201 Created
   {
     study_id, report_id,
     prediction, urgency,
     pdf_url (signed, 15 min)
   }

   Modal closes, new row
   appears in dashboard
   table

   User clicks "View Report"

   GET /api/v1/studies/{id}/report.pdf ─▶
                                          │ Verify access (org_id + assignment)
                                          │ Generate fresh signed URL (15 min)
   ◀───── 302 Redirect to signed URL

   Browser follows redirect ───────────────────────────────────────▶ Firebase
                                                                       │
   ◀──────────── PDF binary ───────────────────────────────────────────│

   Browser displays/downloads PDF
```

---

## DIAGRAM 6 — WHERE EACH FILE LIVES (DEPLOYMENT TOPOLOGY)

```
   AMADOR'S LAPTOP                            PRODUCTION
   ───────────────                            ──────────

   ~/.scanguru-secrets/
     scanguru-ai-903a4-                       Firebase Console
     firebase-adminsdk-                       (UI for managing
     fbsvc-8154d6b79a.json                     service accounts)
        │
        │ base64 encoded → FIREBASE_CREDENTIALS_B64
        ▼

   Password Manager                           Railway env vars
     - JWT_SECRET            ────────────▶     - JWT_SECRET
     - PHI_ENCRYPTION_KEY    ────────────▶     - PHI_ENCRYPTION_KEY
     - admin password        ────────────▶     (used by seed only)
     - POSTGRES_PASSWORD     ────────────▶     (auto via reference var)


   ~/Desktop/.../scanguru-portal/
     ├─ app/                  git push      Railway service:
     ├─ alembic/              ────────▶     scanguru-portal-api
     ├─ requirements.txt                    (auto-builds via
     ├─ Dockerfile                          Dockerfile)
     ├─ .env (local only)                          │
     └─ .gitignore (excludes .env)                 │ runs
                                                   ▼
                                              [Container]
                                              Gunicorn + Uvicorn
                                              port 8000


   ~/Desktop/.../ScanGuru-web/
     ├─ portal-login.html     git push      GitHub Pages:
     ├─ dashboard-main.html   ────────▶     sap-amador.github.io/
     ├─ patient-detail.html                 ScanGuru-web/
     ├─ patients.html                       (static file CDN)
     └─ assets/
```

---

## CHANGELOG

| Date | Change |
|---|---|
| 2026-05-24 | Initial diagrams from deployment build |
