# ScanGuru Portal API

FastAPI backend for the ScanGuru doctor portal. Provides authentication,
patient/study/report management, AI service integration, and signed PDF
delivery to the [frontend](https://github.com/sap-amador/ScanGuru-web).

## Architecture

- FastAPI + SQLAlchemy 2.0 + Alembic
- Postgres (10 tables: orgs, users, patients, studies, reports, audit_log, etc.)
- JWT auth (bcrypt + TOTP scaffolded)
- Fernet PHI encryption at rest
- Storage adapter: local | Firebase | S3
- Calls external AI inference service via HTTP (see AI_SERVICE_URL)

## Deployment

See [`docs/DEVELOPER_DEPLOYMENT_GUIDE.md`](docs/DEVELOPER_DEPLOYMENT_GUIDE.md)
for full step-by-step instructions covering:

- Local sanity check
- Cloud storage setup (Firebase / S3)
- Railway deployment
- Production hardening checklist

## Companion repos

- Frontend: https://github.com/sap-amador/ScanGuru-web
- AI inference: https://github.com/sap-amador/MedScan-AI
