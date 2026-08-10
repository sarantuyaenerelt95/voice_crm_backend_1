"""Add reset_attempts column for OTP-style password reset

Revision ID: b7e2c1f9a4d6
Revises: a1f4d8b2c9e3
Create Date: 2026-07-31

"""
from alembic import op
import sqlalchemy as sa

revision = 'b7e2c1f9a4d6'
down_revision = 'a1f4d8b2c9e3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('reset_attempts', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('users', 'reset_attempts')
