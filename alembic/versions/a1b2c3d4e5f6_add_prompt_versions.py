"""add prompt_versions table

Revision ID: a1b2c3d4e5f6
Revises: 5b938c49b5b9
Create Date: 2026-05-12 03:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "5b938c49b5b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", "version", name="uq_prompt_name_version"),
    )
    op.create_index("ix_prompt_name_active", "prompt_versions", ["name", "is_active"])
    op.create_index("ix_prompt_versions_name", "prompt_versions", ["name"])


def downgrade() -> None:
    op.drop_index("ix_prompt_versions_name", table_name="prompt_versions")
    op.drop_index("ix_prompt_name_active", table_name="prompt_versions")
    op.drop_table("prompt_versions")
