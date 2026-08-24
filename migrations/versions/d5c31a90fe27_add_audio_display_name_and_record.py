"""Add audio display_name and the 'record' audio source

Purely additive:

* `audio_files.display_name` backs renaming. `filename` stays untouched by a
  rename because it is the key Asterisk plays back (Playback(custom/<name>)),
  so changing it would break campaigns that already point at the row.
* `audiosource` gains 'record' for microphone recordings made in the browser.

Revision ID: d5c31a90fe27
Revises: b7e2c1f9a4d6
Create Date: 2026-08-24

"""
from alembic import op
import sqlalchemy as sa

revision = 'd5c31a90fe27'
down_revision = 'b7e2c1f9a4d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'audio_files',
        sa.Column('display_name', sa.String(length=200), nullable=True),
    )

    # Postgres 12+ allows ADD VALUE inside a transaction as long as the new
    # value is not used in that same transaction, which is the case here.
    op.execute("ALTER TYPE audiosource ADD VALUE IF NOT EXISTS 'record'")


def downgrade() -> None:
    # Any rows written as 'record' become 'upload' so the enum value can go.
    op.execute("UPDATE audio_files SET source = 'upload' WHERE source = 'record'")
    op.drop_column('audio_files', 'display_name')

    # Postgres has no DROP VALUE, so the type is rebuilt without 'record'.
    op.execute("ALTER TYPE audiosource RENAME TO audiosource_old")
    op.execute("CREATE TYPE audiosource AS ENUM ('tts', 'upload')")
    op.execute(
        "ALTER TABLE audio_files "
        "ALTER COLUMN source TYPE audiosource "
        "USING source::text::audiosource"
    )
    op.execute("DROP TYPE audiosource_old")
