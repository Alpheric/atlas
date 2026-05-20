"""add_vertex_grounding

Revision ID: c5b82e1d4a09
Revises: a3f19dc4b7e1
Create Date: 2026-05-02 14:00:00.000000

Adds grounding / Vertex AI columns to routing_decisions:
  - web_search_grounded   (bool)  — true when Gemini web search grounding was used
  - grounding_metadata    (text)  — JSON blob with groundingChunks + webSearchQueries
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5b82e1d4a09"
down_revision: Union[str, None] = "a3f19dc4b7e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "routing_decisions",
        sa.Column(
            "web_search_grounded",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "routing_decisions",
        sa.Column("grounding_metadata", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("routing_decisions", "grounding_metadata")
    op.drop_column("routing_decisions", "web_search_grounded")
