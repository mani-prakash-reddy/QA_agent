"""Enable pgvector and Apache AGE extensions

Revision ID: a1b2c3d4e5f6
Revises: fe56fa70289e
Create Date: 2026-10-15 00:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "fe56fa70289e"
branch_labels = None
depends_on = None


def upgrade():
    # Also created by db/init-extensions.sql on a fresh volume; this covers existing DBs.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS age")


def downgrade():
    op.execute("DROP EXTENSION IF EXISTS age")
    op.execute("DROP EXTENSION IF EXISTS vector")
