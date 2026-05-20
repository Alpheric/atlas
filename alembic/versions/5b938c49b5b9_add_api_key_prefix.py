"""add api_keys.key_prefix

Revision ID: 5b938c49b5b9
Revises: e904f11f09fe
Create Date: 2026-05-15 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5b938c49b5b9"
down_revision: Union[str, None] = "e904f11f09fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("key_prefix", sa.String(length=64), nullable=True),
    )
    # Backfill existing rows: we don't have the raw key, only the user's email.
    # Keys are generated as sk-atlas-<slug>-<random>, where slug =
    # email.split('@')[0].replace('.', '-')[:12]. Fill the random portion with
    # '***' so users can still recognize whose key it is.
    op.execute(
        """
        UPDATE api_keys ak
        SET key_prefix =
            'sk-atlas-'
            || LEFT(REPLACE(SPLIT_PART(u.email, '@', 1), '.', '-'), 12)
            || '-***'
        FROM users u
        WHERE ak.user_id = u.id AND ak.key_prefix IS NULL
        """
    )
    op.execute(
        """
        UPDATE api_keys
        SET key_prefix = 'sk-atlas-***'
        WHERE key_prefix IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("api_keys", "key_prefix")
