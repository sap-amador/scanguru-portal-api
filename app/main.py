"""FastAPI entrypoint for the ScanGuru portal backend."""
import logging

# ============================================================================
# PATCH:sentry-backend v1
# Sentry error monitoring. Must run BEFORE FastAPI app instantiation so the
# SDK can hook middleware on app startup.
# DSN is read from env var SENTRY_DSN. If unset, Sentry is a no-op.
# ============================================================================
import os as _sentry_os

_sentry_dsn = _sentry_os.environ.get("SENTRY_DSN", "").strip()
if _sentry_dsn:
    import sentry_sdk as _sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration as _SentryFastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration as _SentryStarletteIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration as _SentrySqlalchemyIntegration

    # PHI scrubber — strip likely-sensitive data BEFORE the event leaves the process.
    # This is belt-and-suspenders on top of send_default_pii=False.
    _PHI_HEADER_KEYS = {"authorization", "cookie", "x-api-key", "set-cookie"}
    _PHI_QUERY_KEYS = {"token", "access_token", "jwt", "mrn", "patient_id"}

    def _scrub_event(event, hint):
        try:
            req = event.get("request") or {}
            # Strip auth-like headers
            headers = req.get("headers") or {}
            if isinstance(headers, dict):
                for k in list(headers.keys()):
                    if k.lower() in _PHI_HEADER_KEYS:
                        headers[k] = "[Filtered]"
            # Strip auth-like query string params
            qs = req.get("query_string")
            if isinstance(qs, str):
                for k in _PHI_QUERY_KEYS:
                    qs = _re_sentry.sub(rf"({k}=)[^&]*", r"\1[Filtered]", qs, flags=_re_sentry.IGNORECASE)
                req["query_string"] = qs
            # Don't ship request bodies at all — they may contain MRN/name on uploads
            if "data" in req:
                req["data"] = "[Filtered]"
        except Exception:
            # Never let scrubbing throw — Sentry would silently drop the event
            pass
        return event

    import re as _re_sentry

    _sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=_sentry_os.environ.get("ENV", "prod"),
        release=_sentry_os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown")[:12],
        traces_sample_rate=0.10,
        profiles_sample_rate=0.0,
        send_default_pii=False,
        max_breadcrumbs=50,
        before_send=_scrub_event,
        integrations=[
            _SentryFastApiIntegration(transaction_style="endpoint"),
            _SentryStarletteIntegration(transaction_style="endpoint"),
            _SentrySqlalchemyIntegration(),
        ],
    )
# ==================== /PATCH:sentry-backend v1 ====================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import auth as auth_router

from datetime import datetime as _hf_datetime, timezone as _hf_timezone
import httpx as _hf_httpx
from sqlalchemy import text as _hf_sa_text
from fastapi import status as _hf_status
from fastapi.responses import JSONResponse as _hf_JSONResponse
from app.database import SessionLocal as _hf_SessionLocal
from app.config import settings as _hf_settings
from app.routers import dashboard as dashboard_router
from app.routers import patients as patients_router
from app.routers import studies as studies_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="ScanGuru Portal API",
    version="0.1.0",
    docs_url=None if settings.env == "prod" else "/docs",
    redoc_url=None if settings.env == "prod" else "/redoc",
    openapi_url=None if settings.env == "prod" else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(dashboard_router.router, prefix="/api/v1/dashboard", tags=["dashboard"])
app.include_router(studies_router.router, prefix="/api/v1/studies", tags=["studies"])
app.include_router(patients_router.router, prefix="/api/v1/patients", tags=["patients"])


# PATCH:health-head v1 — accept HEAD too so HEAD-only monitors don't 405
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "healthy", "service": "scanguru-portal", "version": "0.1.0"}


@app.get("/")
def root():
    return {
        "service": "ScanGuru Portal API",
        "endpoints": [
            "POST /api/v1/auth/login",
            "GET  /api/v1/auth/me",
            "GET  /api/v1/dashboard/stats",
            "GET  /api/v1/studies",
            "POST /api/v1/studies",
            "GET  /api/v1/studies/{id}",
            "GET  /api/v1/studies/{id}/report.pdf",
            "POST /api/v1/studies/{id}/review",
            "GET  /api/v1/patients/{id}",
            "GET  /api/v1/patients/{id}/timeline",
        ],
    }

# ============================================================================
# PATCH:health-full v1
# Aggregate health check — covers portal, database, AI service.
# Used by external uptime monitoring (UptimeRobot, BetterStack, etc.).
# Returns HTTP 200 if all components healthy, HTTP 503 if any are down.
# Always completes within ~6 seconds (5s for AI HTTP + 1s for DB query).
# ============================================================================
# PATCH:health-full-head v1 — accept HEAD too for UptimeRobot free tier
@app.api_route("/health/full", methods=["GET", "HEAD"])
def health_full():
    components = {}
    overall_ok = True

    # --- portal: trivially up (we're executing this code, so the process is up)
    components["portal"] = {"status": "up"}

    # --- database: short SELECT 1, 1s effective timeout via statement_timeout
    db_start = _hf_datetime.now(_hf_timezone.utc)
    try:
        db = _hf_SessionLocal()
        try:
            db.execute(_hf_sa_text("SET LOCAL statement_timeout = 1000"))
            db.execute(_hf_sa_text("SELECT 1"))
            db_ms = int((_hf_datetime.now(_hf_timezone.utc) - db_start).total_seconds() * 1000)
            components["database"] = {"status": "up", "latency_ms": db_ms}
        finally:
            db.close()
    except Exception as exc:
        components["database"] = {"status": "down", "error": str(exc)[:200]}
        overall_ok = False

    # --- ai service: GET its /health with a 5s timeout
    ai_url = (_hf_settings.ai_service_url or "").rstrip("/") + "/health"
    ai_start = _hf_datetime.now(_hf_timezone.utc)
    try:
        with _hf_httpx.Client(timeout=5.0) as client:
            r = client.get(ai_url)
        ai_ms = int((_hf_datetime.now(_hf_timezone.utc) - ai_start).total_seconds() * 1000)
        if 200 <= r.status_code < 300:
            components["ai"] = {"status": "up", "latency_ms": ai_ms, "url": ai_url}
        else:
            components["ai"] = {
                "status": "down",
                "latency_ms": ai_ms,
                "url": ai_url,
                "error": f"HTTP {r.status_code}",
            }
            overall_ok = False
    except Exception as exc:
        components["ai"] = {"status": "down", "url": ai_url, "error": str(exc)[:200]}
        overall_ok = False

    payload = {
        "status": "healthy" if overall_ok else "degraded",
        "components": components,
        "checked_at": _hf_datetime.now(_hf_timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    http_code = _hf_status.HTTP_200_OK if overall_ok else _hf_status.HTTP_503_SERVICE_UNAVAILABLE
    return _hf_JSONResponse(status_code=http_code, content=payload)
# ==================== /PATCH:health-full v1 ====================

