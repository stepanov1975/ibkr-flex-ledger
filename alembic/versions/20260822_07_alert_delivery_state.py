"""Persist outbound alert delivery state per account, channel, and destination."""

from alembic import op
import sqlalchemy as sa


revision = "20260822_07"
down_revision = "20260822_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_delivery_state",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("destination_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("alerting", sa.Boolean(), nullable=False),
        sa.Column("transition_anchor_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_delivered_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("channel IN ('webhook', 'email')", name="ck_alert_delivery_state_channel"),
        sa.PrimaryKeyConstraint(
            "account_id",
            "channel",
            "destination_fingerprint",
            name="pk_alert_delivery_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("alert_delivery_state")
