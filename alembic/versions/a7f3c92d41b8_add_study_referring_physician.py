"""add study.referring_physician

The intake form has been collecting a referring physician since the portal
launched, but ``create_study`` never declared it as a parameter, so FastAPI
discarded it silently. This adds somewhere to put it.

Deliberately free text rather than a FK to ``users``. A referring physician is
usually external — the GP or clinic who sent the patient in — and will not have
a portal login. ``PatientAssignment`` already models the internal
"which of our users owns this patient" relationship; this column models
"who sent them", which is a different question.

Nullable with no default: every existing study genuinely has no referrer
recorded, and backfilling a placeholder would invent data.

Revision ID: a7f3c92d41b8
Revises: 11c53e890958
Create Date: 2026-07-25

"""
from alembic import op
import sqlalchemy as sa


revision = "a7f3c92d41b8"
down_revision = "11c53e890958"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "studies",
        sa.Column("referring_physician", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("studies", "referring_physician")
