"""Add call token billing

Purely additive. Creates the token package/purchase/ledger tables and the
balance columns. It does not touch the SMS tables: removing those is a separate,
destructive migration so it can be reviewed and run on its own.

Revision ID: cebf86117d29
Revises: 7a95faad449f
Create Date: 2026-07-28 06:02:49.304656

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'cebf86117d29'
down_revision: Union[str, None] = '7a95faad449f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# create_type=False: the types are created once explicitly in upgrade().
# Without it, each create_table() would emit its own CREATE TYPE and the
# second one fails with "type already exists".
purchase_status = postgresql.ENUM(
    'pending', 'paid', 'failed', 'cancelled',
    name='purchasestatus',
    create_type=False,
)

ledger_entry_type = postgresql.ENUM(
    'purchase', 'reserve', 'commit', 'release', 'adjustment', 'refund',
    name='ledgerentrytype',
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    purchase_status.create(bind, checkfirst=True)
    ledger_entry_type.create(bind, checkfirst=True)

    op.create_table(
        'token_packages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('call_count', sa.Integer(), nullable=True),
        sa.Column('price_mnt', sa.BigInteger(), nullable=True),
        sa.Column('per_call_mnt', sa.BigInteger(), nullable=False),
        sa.Column('is_custom_quantity', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('min_call_count', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index(op.f('ix_token_packages_id'), 'token_packages', ['id'], unique=False)

    op.create_table(
        'token_purchases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('package_id', sa.Integer(), nullable=True),
        sa.Column('call_count', sa.Integer(), nullable=False),
        sa.Column('amount_mnt', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='MNT'),
        sa.Column('status', purchase_status, nullable=False, server_default='pending'),
        sa.Column('payment_provider', sa.String(length=50), nullable=False, server_default='manual'),
        sa.Column('provider_ref', sa.String(length=200), nullable=True),
        sa.Column('provider_payload', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['package_id'], ['token_packages.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_token_purchases_id'), 'token_purchases', ['id'], unique=False)
    op.create_index(op.f('ix_token_purchases_company_id'), 'token_purchases', ['company_id'], unique=False)
    op.create_index(op.f('ix_token_purchases_status'), 'token_purchases', ['status'], unique=False)
    op.create_index(op.f('ix_token_purchases_provider_ref'), 'token_purchases', ['provider_ref'], unique=False)

    op.create_table(
        'token_ledger',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('entry_type', ledger_entry_type, nullable=False),
        sa.Column('delta_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('delta_reserved', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tokens_after', sa.Integer(), nullable=False),
        sa.Column('reserved_after', sa.Integer(), nullable=False),
        sa.Column('call_log_id', sa.Integer(), nullable=True),
        sa.Column('campaign_id', sa.Integer(), nullable=True),
        sa.Column('purchase_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['call_log_id'], ['call_logs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['purchase_id'], ['token_purchases.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_token_ledger_id'), 'token_ledger', ['id'], unique=False)
    op.create_index(op.f('ix_token_ledger_company_id'), 'token_ledger', ['company_id'], unique=False)
    op.create_index(op.f('ix_token_ledger_entry_type'), 'token_ledger', ['entry_type'], unique=False)
    op.create_index(op.f('ix_token_ledger_call_log_id'), 'token_ledger', ['call_log_id'], unique=False)
    op.create_index(op.f('ix_token_ledger_campaign_id'), 'token_ledger', ['campaign_id'], unique=False)
    op.create_index(op.f('ix_token_ledger_purchase_id'), 'token_ledger', ['purchase_id'], unique=False)
    op.create_index(op.f('ix_token_ledger_created_at'), 'token_ledger', ['created_at'], unique=False)

    # server_default is required: these tables already hold rows, and a NOT NULL
    # column cannot be added to them without one.
    op.add_column(
        'call_logs',
        sa.Column('token_state', sa.String(length=20), nullable=False, server_default='none'),
    )
    op.create_index(op.f('ix_call_logs_token_state'), 'call_logs', ['token_state'], unique=False)

    op.add_column(
        'companies',
        sa.Column('call_tokens', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'companies',
        sa.Column('reserved_tokens', sa.Integer(), nullable=False, server_default='0'),
    )

    # Seed the three packages.
    op.execute(
        """
        INSERT INTO token_packages
            (code, name, sort_order, call_count, price_mnt, per_call_mnt,
             is_custom_quantity, min_call_count, is_active)
        VALUES
            ('bagts_1', 'Багц 1', 1,   55,  10000, 182, false, NULL, true),
            ('bagts_2', 'Багц 2', 2,  190,  30000, 158, false, NULL, true),
            ('bagts_3', 'Багц 3', 3, NULL,   NULL, 150, true,   191, true)
        """
    )


def downgrade() -> None:
    op.drop_column('companies', 'reserved_tokens')
    op.drop_column('companies', 'call_tokens')
    op.drop_index(op.f('ix_call_logs_token_state'), table_name='call_logs')
    op.drop_column('call_logs', 'token_state')

    op.drop_table('token_ledger')
    op.drop_table('token_purchases')
    op.drop_table('token_packages')

    bind = op.get_bind()
    ledger_entry_type.drop(bind, checkfirst=True)
    purchase_status.drop(bind, checkfirst=True)
