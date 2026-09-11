"""Source-bound security transfers and distribution opening lots."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260911_18'
down_revision = '20260911_17'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'corporate_action_resolution',
        sa.Column('event_corp_action_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('event_corp_action.event_corp_action_id'), primary_key=True),
        sa.Column('source_signature', sa.Text(), nullable=False),
        sa.Column('source_instrument_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('instrument.instrument_id')),
        sa.Column('destination_instrument_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('instrument.instrument_id'), nullable=False),
        sa.Column('report_date_local', sa.Date(), nullable=False),
        sa.Column('quantity', sa.Numeric(24, 8), nullable=False),
        sa.Column('cost_basis', sa.Numeric(24, 8)),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('treatment', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('invalidated_reason', sa.Text()),
        sa.Column('updated_at_utc', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("quantity > 0 AND quantity <> 'NaN'::numeric", name='ck_ca_resolution_quantity'),
        sa.CheckConstraint("(treatment='security_transfer' AND source_instrument_id IS NOT NULL "
                           "AND source_instrument_id<>destination_instrument_id AND cost_basis IS NULL) OR "
                           "(treatment='distribution' AND source_instrument_id IS NULL AND cost_basis IS NOT NULL "
                           "AND cost_basis >= 0 AND cost_basis <> 'NaN'::numeric)", name='ck_ca_resolution_treatment'),
    )
    op.add_column('position_lot', sa.Column('open_event_corp_action_id', postgresql.UUID(as_uuid=True)))
    op.create_foreign_key('fk_position_lot_corporate_action', 'position_lot', 'event_corp_action',
                          ['open_event_corp_action_id'], ['event_corp_action_id'])
    op.alter_column('position_lot', 'open_event_trade_fill_id', nullable=True)
    op.create_check_constraint('ck_position_lot_opening_event', 'position_lot',
                               'open_event_trade_fill_id IS NOT NULL OR open_event_corp_action_id IS NOT NULL')


def downgrade() -> None:
    # A downgrade cannot represent distribution lots; fail instead of deleting financial records.
    op.alter_column('position_lot', 'open_event_trade_fill_id', nullable=False)
    op.drop_constraint('ck_position_lot_opening_event', 'position_lot', type_='check')
    op.drop_constraint('fk_position_lot_corporate_action', 'position_lot', type_='foreignkey')
    op.drop_column('position_lot', 'open_event_corp_action_id')
    op.drop_table('corporate_action_resolution')
