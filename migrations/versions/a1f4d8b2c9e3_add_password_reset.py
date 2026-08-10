"""Add password reset token columns

Revision ID: a1f4d8b2c9e3
Revises: cebf86117d29
Create Date: 2026-07-31

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1f4d8b2c9e3'
down_revision = 'cebf86117d29'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('reset_token_hash', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('reset_token_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_users_reset_token_hash'), 'users', ['reset_token_hash'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_reset_token_hash'), table_name='users')
    op.drop_column('users', 'reset_token_expires_at')
    op.drop_column('users', 'reset_token_hash')
