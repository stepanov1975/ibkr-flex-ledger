"""Repair understated run completion times from retained terminal diagnostics."""

from alembic import op

revision = "20261117_19"
down_revision = "20260911_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Archived or unparseable diagnostics cannot establish a better timestamp.
    op.execute("""
        WITH terminal AS (
            SELECT ingestion_run_id, CASE WHEN pg_input_is_valid(
                diagnostics->-1->>'at_utc', 'timestamp with time zone'
            ) THEN (diagnostics->-1->>'at_utc')::timestamptz END AS finished
            FROM ingestion_run WHERE status IN ('success','failed')
            AND diagnostics->-1->>'stage'='run' AND diagnostics->-1->>'status'=status
        )
        UPDATE ingestion_run r SET ended_at_utc=t.finished,
            duration_ms=(extract(epoch FROM t.finished-r.started_at_utc)*1000)::bigint
        FROM terminal t WHERE r.ingestion_run_id=t.ingestion_run_id
        AND isfinite(t.finished) AND t.finished>r.ended_at_utc AND t.finished>=r.started_at_utc
    """)


def downgrade() -> None:
    """Corrected timestamps remain valid on older schemas."""
