"""Free-tier scan metering — the load-bearing logic of 'Heal for All'.

`check_and_reserve()` is called when a scan actually consumes compute (in the
studies analyze path). It atomically increments the org's monthly counter and
applies a SOFT limit for verified mission orgs: warn at 80%, never hard-block
mid-care. `usage_counters` is the single honest source of truth — the same
number that enforces the quota is the number reported to funders, so there is
nothing to inflate.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Org, UsageCounter, AuditLog


@dataclass
class QuotaResult:
    ok: bool            # may the scan proceed?
    used: int
    quota: int
    warn: bool = False  # at/over the warn threshold (e.g. 80%)
    over: bool = False  # over quota but allowed (verified mission org, soft limit)


def first_of_month_utc() -> date:
    now = datetime.now(timezone.utc)
    return date(now.year, now.month, 1)


def check_and_reserve(db: Session, org: Org) -> QuotaResult:
    """Increment this org's monthly tally by one and decide whether the scan proceeds.

    Atomic upsert+increment via ON CONFLICT, so concurrent uploads can't race the
    counter. The (org_id, period_month) primary key on usage_counters powers the
    conflict target — see migrations/001_heal_for_all_free_tier.sql.
    """
    period = first_of_month_utc()
    used = db.execute(
        text("""
            INSERT INTO usage_counters (org_id, period_month, scans_used)
            VALUES (:org_id, :period, 1)
            ON CONFLICT (org_id, period_month)
            DO UPDATE SET scans_used = usage_counters.scans_used + 1
            RETURNING scans_used
        """),
        {"org_id": str(org.id), "period": period},
    ).scalar_one()
    db.commit()

    quota = org.monthly_scan_quota
    if used <= quota:
        return QuotaResult(ok=True, used=used, quota=quota,
                           warn=used >= settings.warn_threshold * quota)

    # Over quota.
    if org.free_tier_verified:
        # SOFT limit: never block care for a verified mission org. Flag for follow-up.
        _flag_overage(db, org, used, quota)
        return QuotaResult(ok=True, used=used, quota=quota, over=True)

    # Paid / unverified orgs: block and let the route surface an upsell.
    return QuotaResult(ok=False, used=used, quota=quota)


def _flag_overage(db: Session, org: Org, used: int, quota: int) -> None:
    """Record an overage so a human can reach out about a larger free allocation.
    A clinic over its cap usually means it's helping more people — that's the point."""
    db.add(AuditLog(
        org_id=org.id, actor_user_id=None, action="quota.overage",
        resource_type="org", resource_id=org.id, success=True,
        extra={"used": used, "quota": quota},
    ))
    db.commit()
    # TODO (optional): also notify the ScanGuru team (email/Slack) here.


def current_usage(db: Session, org: Org) -> QuotaResult:
    """Read-only snapshot for the /orgs/me/usage meter — does NOT increment."""
    period = first_of_month_utc()
    row = db.get(UsageCounter, {"org_id": org.id, "period_month": period})
    used = row.scans_used if row else 0
    quota = org.monthly_scan_quota
    return QuotaResult(
        ok=used < quota, used=used, quota=quota,
        warn=used >= settings.warn_threshold * quota, over=used > quota,
    )
