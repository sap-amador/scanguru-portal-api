"""add org access_mode for shared-clinic flow

Revision ID: 11c53e890958
Revises: 66b4e65ebc4e
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '11c53e890958'
down_revision: Union[str, None] = '66b4e65ebc4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    accessmode = sa.Enum('shared', 'per_doctor', name='accessmode')
    accessmode.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'orgs',
        sa.Column(
            'access_mode',
            accessmode,
            server_default='shared',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('orgs', 'access_mode')
    sa.Enum(name='accessmode').drop(op.get_bind(), checkfirst=True)
