-- 001_heal_for_all_free_tier.sql
-- REQUIRED. Run once against the Railway Postgres for the portal API.
-- Adds the Heal-for-All free-tier columns to orgs and creates usage_counters.
-- Idempotent (IF NOT EXISTS), safe to run more than once.

ALTER TABLE orgs
  ADD COLUMN IF NOT EXISTS free_tier_type        varchar(32),
  ADD COLUMN IF NOT EXISTS free_tier_verified    boolean      NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS free_tier_verified_by uuid REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS free_tier_verified_at timestamptz,
  ADD COLUMN IF NOT EXISTS monthly_scan_quota    integer      NOT NULL DEFAULT 100;

CREATE TABLE IF NOT EXISTS usage_counters (
  org_id       uuid    NOT NULL REFERENCES orgs(id),
  period_month date    NOT NULL,                 -- first day of the month, UTC
  scans_used   integer NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, period_month)             -- also the ON CONFLICT target in quota.py
);
