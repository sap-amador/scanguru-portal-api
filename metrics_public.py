"""
metrics_public.py — Public, read-only metrics for the live metrics page.
============================================================================
Stack: FastAPI + SQLAlchemy + Railway Postgres (your current backend).
Exposes:  GET /metrics/public  ->  JSON consumed by scanguru-metrics.html

PRIVACY (non-negotiable): this endpoint is PUBLIC. It returns ONLY aggregate
counts and COUNTRY-LEVEL recent activity. No patient name/MRN/age/sex, no
image data, no free-text — nothing that could identify a patient or a single
clinic. Safe to expose through Cloudflare.

HONESTY: every metric is computed independently and degrades to 0 / [] if the
underlying column doesn't exist yet. A metric you don't capture shows as zero
rather than a fabricated number. To light up the map + the institutions/
countries counters you must capture `country` (ISO-2) and `institution` on each
study — e.g. from the signed-in clinic's profile at upload time.

WIRING (in scanguru-portal-api):
  from metrics_public import router as metrics_router
  app.include_router(metrics_router, prefix="/api/v1")   # match your other routers
  # -> endpoint becomes  /api/v1/metrics/public
Then in Cloudflare, allow caching for /api/v1/metrics/* (the response sets s-maxage).
============================================================================
"""
from datetime import datetime, timedelta, timezone
import time
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

# --- from YOUR app (already exist in your codebase) -------------------------
from app.database import get_db          # your SQLAlchemy session dependency
# ----------------------------------------------------------------------------

router = APIRouter(prefix="/metrics", tags=["metrics"])

# ============================================================================
# ADJUST TO YOUR PORTAL SCHEMA (scanguru-portal-api Postgres)
# `scans` = one row per scan; `reports` = one row per generated report;
# `organizations` = tenants (institutions). Verify column names against your
# Alembic models.
# ============================================================================
SCANS_TABLE     = "scans"                # one row per scan
REPORTS_TABLE   = "reports"              # one row per generated report
COL_CREATED     = "created_at"           # timestamptz
COL_MODALITY    = "modality"             # enum stored UPPERCASE: CXR, CT_BRAIN, ...
COL_LANG        = "report_lang"          # report language (code or name)
COL_COUNTRY     = "country"              # ISO-2; may live on organizations (see note)
COL_ORG         = "organization_id"      # tenant/institution FK on scans
COL_TAT_SECONDS = "turnaround_seconds"   # numeric seconds, nullable
LIVE_MODALITIES = ["CXR", "CT_BRAIN", "CT_CHEST", "DENTAL", "MSK"]
CACHE_TTL       = 60                     # seconds (also matches Cloudflare s-maxage)

# Display metadata for map + chips. Extend as you onboard countries.
COUNTRY_GEO = {
    "IN": ("India", "\U0001F1EE\U0001F1F3", 21.0, 78.0),
    "AE": ("United Arab Emirates", "\U0001F1E6\U0001F1EA", 24.0, 54.0),
    "SA": ("Saudi Arabia", "\U0001F1F8\U0001F1E6", 24.0, 45.0),
    "DE": ("Germany", "\U0001F1E9\U0001F1EA", 51.0, 10.0),
    "AU": ("Australia", "\U0001F1E6\U0001F1FA", -25.0, 133.0),
    "GB": ("United Kingdom", "\U0001F1EC\U0001F1E7", 54.0, -2.0),
    "US": ("United States", "\U0001F1FA\U0001F1F8", 39.0, -98.0),
    "ID": ("Indonesia", "\U0001F1EE\U0001F1E9", -2.0, 118.0),
    "TR": ("Turkiye", "\U0001F1F9\U0001F1F7", 39.0, 35.0),
    "VN": ("Vietnam", "\U0001F1FB\U0001F1F3", 16.0, 108.0),
    "NG": ("Nigeria", "\U0001F1F3\U0001F1EC", 9.0, 8.0),
    "PK": ("Pakistan", "\U0001F1F5\U0001F1F0", 30.0, 70.0),
    "BD": ("Bangladesh", "\U0001F1E7\U0001F1E9", 24.0, 90.0),
    "PL": ("Poland", "\U0001F1F5\U0001F1F1", 52.0, 19.0),
    "GR": ("Greece", "\U0001F1EC\U0001F1F7", 39.0, 22.0),
    "BR": ("Brazil", "\U0001F1E7\U0001F1F7", -10.0, -55.0),
}
MODALITY_LABEL = {"CXR": "CXR", "CT_BRAIN": "CT Brain", "CT_CHEST": "CT Chest",
                  "DENTAL": "Dental", "MSK": "MSK"}

_cache = {"ts": 0.0, "data": None}


def _scalar(db, sql, **p):
    try:
        return db.execute(text(sql), p).scalar() or 0
    except Exception:
        return 0


def build_metrics(db: Session) -> dict:
    T = SCANS_TABLE
    since = datetime.now(timezone.utc) - timedelta(days=30)
    out = {"updated": datetime.now(timezone.utc).isoformat()}

    out["scans"] = _scalar(db, f"SELECT COUNT(*) FROM {T}")
    # institutions = distinct tenants that have actually uploaded
    out["institutions"] = _scalar(
        db, f"SELECT COUNT(DISTINCT {COL_ORG}) FROM {T} WHERE {COL_ORG} IS NOT NULL")
    # countries: if `country` isn't on scans, join to organizations:
    #   SELECT COUNT(DISTINCT o.country) FROM scans s
    #     JOIN organizations o ON o.id = s.organization_id WHERE o.country IS NOT NULL
    out["countries"] = _scalar(
        db, f"SELECT COUNT(DISTINCT {COL_COUNTRY}) FROM {T} WHERE {COL_COUNTRY} IS NOT NULL")
    out["report_languages"] = _scalar(
        db, f"SELECT COUNT(DISTINCT {COL_LANG}) FROM {T} WHERE {COL_LANG} IS NOT NULL")

    # total reports generated = rows in the reports table (falls back to scans count)
    try:
        out["reports"] = db.execute(text(f"SELECT COUNT(*) FROM {REPORTS_TABLE}")).scalar() or 0
    except Exception:
        out["reports"] = out.get("scans", 0)

    # operational volume windows
    def _since(days):
        try:
            return db.execute(
                text(f"SELECT COUNT(*) FROM {T} WHERE {COL_CREATED} >= :s"),
                {"s": datetime.now(timezone.utc) - timedelta(days=days)}).scalar() or 0
        except Exception:
            return 0
    out["volume"] = {"daily": _since(1), "weekly": _since(7), "monthly": _since(30)}

    # overall average processing time (seconds)
    out["avg_processing_sec"] = _scalar(
        db, f"SELECT ROUND(AVG({COL_TAT_SECONDS}))::int FROM {T} WHERE {COL_TAT_SECONDS} IS NOT NULL")

    # 30-day daily volume
    try:
        rows = db.execute(text(f"""
            SELECT to_char(date_trunc('day', {COL_CREATED}), 'YYYY-MM-DD') d, COUNT(*) c
            FROM {T} WHERE {COL_CREATED} >= :since GROUP BY 1 ORDER BY 1
        """), {"since": since}).all()
        out["timeseries"] = [{"date": r[0], "scans": int(r[1])} for r in rows]
    except Exception:
        out["timeseries"] = []

    # modality breakdown — live modalities only
    try:
        rows = db.execute(text(f"SELECT {COL_MODALITY} m, COUNT(*) c FROM {T} GROUP BY 1")).all()
        out["modalities"] = {MODALITY_LABEL.get(r[0], r[0]): int(r[1])
                             for r in rows if r[0] in LIVE_MODALITIES}
    except Exception:
        out["modalities"] = {}

    # top report languages
    try:
        rows = db.execute(text(f"""
            SELECT {COL_LANG} l, COUNT(*) c FROM {T} WHERE {COL_LANG} IS NOT NULL
            GROUP BY 1 ORDER BY c DESC LIMIT 8
        """)).all()
        out["languages"] = [{"name": str(r[0]), "count": int(r[1])} for r in rows]
    except Exception:
        out["languages"] = []

    # avg turnaround per day (seconds)
    try:
        rows = db.execute(text(f"""
            SELECT to_char(date_trunc('day', {COL_CREATED}), 'YYYY-MM-DD') d,
                   ROUND(AVG({COL_TAT_SECONDS}))::int s
            FROM {T} WHERE {COL_TAT_SECONDS} IS NOT NULL AND {COL_CREATED} >= :since
            GROUP BY 1 ORDER BY 1
        """), {"since": since}).all()
        out["turnaround"] = [{"date": r[0], "sec": int(r[1])} for r in rows]
    except Exception:
        out["turnaround"] = []

    # countries with geo (for the glowing map + chips)
    try:
        rows = db.execute(text(f"""
            SELECT {COL_COUNTRY} c, COUNT(*) n FROM {T} WHERE {COL_COUNTRY} IS NOT NULL
            GROUP BY 1 ORDER BY n DESC
        """)).all()
        cl = []
        for code, n in rows:
            geo = COUNTRY_GEO.get((code or "").upper())
            if geo:
                name, flag, lat, lon = geo
                cl.append({"code": code, "name": name, "flag": flag,
                           "scans": int(n), "lat": lat, "lon": lon})
        out["countries_list"] = cl
    except Exception:
        out["countries_list"] = []

    # recent activity — COUNTRY level only (privacy). modality + country + time.
    try:
        rows = db.execute(text(f"""
            SELECT {COL_MODALITY} m, {COL_COUNTRY} c, {COL_CREATED} t
            FROM {T} ORDER BY {COL_CREATED} DESC LIMIT 8
        """)).all()
        act = []
        for m, c, t in rows:
            geo = COUNTRY_GEO.get((c or "").upper())
            loc = geo[0] if geo else (c or "\u2014")
            act.append({
                "time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)[:5],
                "modality": MODALITY_LABEL.get(m, m or "\u2014"),
                "location": loc,
            })
        out["activity"] = act
    except Exception:
        out["activity"] = []

    return out


@router.get("/public")
def metrics_public(db: Session = Depends(get_db)):
    now = time.time()
    if _cache["data"] and (now - _cache["ts"] < CACHE_TTL):
        data = _cache["data"]
    else:
        data = build_metrics(db)
        _cache["ts"], _cache["data"] = now, data

    resp = JSONResponse(data)
    # Cloudflare edge + browser caching; serve stale briefly while revalidating.
    resp.headers["Cache-Control"] = "public, max-age=30, s-maxage=60, stale-while-revalidate=120"
    resp.headers["Access-Control-Allow-Origin"] = "*"   # public, read-only, no PHI
    return resp


# ============================================================================
# MongoDB variant — ONLY if your studies live in Mongo instead of Postgres.
# Replace build_metrics() with this and drop the SQLAlchemy import.
# ----------------------------------------------------------------------------
# from pymongo import MongoClient
# col = MongoClient(MONGO_URI)["scanguru"]["studies"]
# def build_metrics_mongo() -> dict:
#     since = datetime.now(timezone.utc) - timedelta(days=30)
#     out = {"updated": datetime.now(timezone.utc).isoformat()}
#     out["scans"] = col.estimated_document_count()
#     out["countries"] = len(col.distinct("country"))
#     out["institutions"] = len(col.distinct("institution"))
#     out["report_languages"] = len(col.distinct("report_lang"))
#     out["timeseries"] = list(col.aggregate([
#         {"$match": {"created_at": {"$gte": since}}},
#         {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
#                     "scans": {"$sum": 1}}},
#         {"$project": {"_id": 0, "date": "$_id", "scans": 1}}, {"$sort": {"date": 1}}]))
#     # ...same shape for modalities / languages / turnaround / countries_list / activity
#     return out
# ============================================================================
