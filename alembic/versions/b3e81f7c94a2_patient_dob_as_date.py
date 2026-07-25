"""patients.dob: timestamptz -> date

A date of birth is a calendar date, not an instant. Stored as timestamptz it
was written as UTC midnight, which renders as the *previous day* to anyone
reading it from a negative-offset timezone — so the same patient could appear
to be born on the 11th in New York and the 12th in London.

The conversion pins the source to UTC explicitly rather than relying on the
session TimeZone, so it produces the same result regardless of where or when
it is run. Every existing dob was written as UTC midnight by _parse_dob, so
this is lossless for current data.

Revision ID: b3e81f7c94a2
Revises: a7f3c92d41b8
Create Date: 2026-07-25

"""
from alembic import op
import sqlalchemy as sa


revision = "b3e81f7c94a2"
down_revision = "a7f3c92d41b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "patients",
        "dob",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.Date(),
        existing_nullable=True,
        postgresql_using="(dob AT TIME ZONE 'UTC')::date",
    )


def downgrade() -> None:
    # Back to an instant at UTC midnight — the shape the column held before.
    op.alter_column(
        "patients",
        "dob",
        existing_type=sa.Date(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="(dob::timestamp AT TIME ZONE 'UTC')",
    )
