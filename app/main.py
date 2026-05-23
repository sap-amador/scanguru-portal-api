"""FastAPI entrypoint for the ScanGuru portal backend."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import auth as auth_router
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


@app.get("/health")
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
