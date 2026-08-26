"""Add a real 1-call / 100 MNT package

Replaces the disabled QPAY_TEST_PURCHASE_ENABLED path with an ordinary,
permanently-buyable package. Anyone testing the payment connection buys this
like any other package - no special "test" route, flag, or wording anywhere
in the product.

Revision ID: e6f2a1b8c4d7
Revises: d5c31a90fe27
Create Date: 2026-08-26

"""
from alembic import op
import sqlalchemy as sa

revision = 'e6f2a1b8c4d7'
down_revision = 'd5c31a90fe27'
branch_labels = None
depends_on = None

token_packages = sa.table(
    'token_packages',
    sa.column('code', sa.String),
    sa.column('name', sa.String),
    sa.column('sort_order', sa.Integer),
    sa.column('call_count', sa.Integer),
    sa.column('price_mnt', sa.BigInteger),
    sa.column('per_call_mnt', sa.BigInteger),
    sa.column('is_custom_quantity', sa.Boolean),
    sa.column('is_active', sa.Boolean),
)


def upgrade() -> None:
    op.execute(
        token_packages.insert().values(
            code='bagts_single',
            name='1 дуудлага',
            sort_order=0,
            call_count=1,
            price_mnt=100,
            per_call_mnt=100,
            is_custom_quantity=False,
            is_active=True,
        )
    )


def downgrade() -> None:
    op.execute(token_packages.delete().where(token_packages.c.code == 'bagts_single'))
