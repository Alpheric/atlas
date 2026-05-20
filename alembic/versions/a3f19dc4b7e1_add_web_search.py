"""add_web_search

Revision ID: a3f19dc4b7e1
Revises: 2645b64912c2
Create Date: 2026-05-02 12:00:00.000000

Adds the web search layer tables:
  - web_search_runs       — one record per search event
  - web_search_results    — individual results from the provider
  - web_extracted_pages   — cleaned page content for top N results
  - web_citations         — source citations linked to LLM responses
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = 'a3f19dc4b7e1'
down_revision: Union[str, None] = '2645b64912c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect SQLite vs PostgreSQL at migration time."""
    bind = op.get_bind()
    return bind.dialect.name == "sqlite"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # web_search_runs
    # ------------------------------------------------------------------
    op.create_table(
        'web_search_runs',
        sa.Column('id', sa.String(36) if _is_sqlite() else sa.UUID(), nullable=False),
        sa.Column('workspace_id', sa.String(36) if _is_sqlite() else sa.UUID(), nullable=True),
        sa.Column('query_masked', sa.Text(), nullable=False),
        sa.Column('query_raw_hash', sa.String(16), nullable=True),
        sa.Column('provider', sa.String(64), nullable=False),
        sa.Column('result_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('cost_usd', sa.Numeric(10, 6), nullable=False, server_default='0'),
        sa.Column('blocked', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('block_reason', sa.Text(), nullable=True),
        sa.Column('search_reason', sa.String(64), nullable=True),
        sa.Column('atlas_model', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_web_search_runs_workspace', 'web_search_runs', ['workspace_id'])
    op.create_index('ix_web_search_runs_created', 'web_search_runs', ['created_at'])
    op.create_index('ix_web_search_runs_provider', 'web_search_runs', ['provider'])

    # ------------------------------------------------------------------
    # web_search_results
    # ------------------------------------------------------------------
    op.create_table(
        'web_search_results',
        sa.Column('id', sa.String(36) if _is_sqlite() else sa.UUID(), nullable=False),
        sa.Column('run_id', sa.String(36) if _is_sqlite() else sa.UUID(), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('published_date', sa.String(32), nullable=True),
        sa.Column('source', sa.String(256), nullable=True),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('was_extracted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['run_id'], ['web_search_runs.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_web_search_results_run', 'web_search_results', ['run_id'])

    # ------------------------------------------------------------------
    # web_extracted_pages
    # ------------------------------------------------------------------
    op.create_table(
        'web_extracted_pages',
        sa.Column('id', sa.String(36) if _is_sqlite() else sa.UUID(), nullable=False),
        sa.Column('result_id', sa.String(36) if _is_sqlite() else sa.UUID(), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('content_summary', sa.Text(), nullable=True),
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('source_date', sa.String(32), nullable=True),
        sa.Column('extraction_ok', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['result_id'], ['web_search_results.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_web_extracted_pages_result', 'web_extracted_pages', ['result_id'])

    # ------------------------------------------------------------------
    # web_citations
    # ------------------------------------------------------------------
    op.create_table(
        'web_citations',
        sa.Column('id', sa.String(36) if _is_sqlite() else sa.UUID(), nullable=False),
        sa.Column('run_id', sa.String(36) if _is_sqlite() else sa.UUID(), nullable=False),
        sa.Column(
            'routing_decision_id',
            sa.String(36) if _is_sqlite() else sa.UUID(),
            nullable=True,
        ),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('published_date', sa.String(32), nullable=True),
        sa.Column('accessed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claim_supported', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('rank', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['run_id'], ['web_search_runs.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['routing_decision_id'], ['routing_decisions.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_web_citations_run', 'web_citations', ['run_id'])
    op.create_index('ix_web_citations_routing', 'web_citations', ['routing_decision_id'])


    # ------------------------------------------------------------------
    # Add RAG training columns to dual_execution_records
    # ------------------------------------------------------------------
    op.add_column(
        'dual_execution_records',
        sa.Column('has_web_context', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column(
        'dual_execution_records',
        sa.Column('web_search_run_id', sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dual_execution_records', 'web_search_run_id')
    op.drop_column('dual_execution_records', 'has_web_context')
    op.drop_index('ix_web_citations_routing', table_name='web_citations')
    op.drop_index('ix_web_citations_run', table_name='web_citations')
    op.drop_table('web_citations')

    op.drop_index('ix_web_extracted_pages_result', table_name='web_extracted_pages')
    op.drop_table('web_extracted_pages')

    op.drop_index('ix_web_search_results_run', table_name='web_search_results')
    op.drop_table('web_search_results')

    op.drop_index('ix_web_search_runs_provider', table_name='web_search_runs')
    op.drop_index('ix_web_search_runs_created', table_name='web_search_runs')
    op.drop_index('ix_web_search_runs_workspace', table_name='web_search_runs')
    op.drop_table('web_search_runs')
