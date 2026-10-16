"""embedding columns 1024 -> 1536 (Azure OpenAI text-embedding-ada-002)

Stored vectors belong to the old model and are not comparable, so they are cleared and recomputed
lazily (mapping and ingest re-embed whatever is missing or from another model).

Revision ID: 2a1b3c4d5e6f
Revises: 1529ffe4b986
"""

from alembic import op

revision = "2a1b3c4d5e6f"
down_revision = "1529ffe4b986"
branch_labels = None
depends_on = None

TABLES = ["requirement", "ui_state", "test_case"]


def _retype(dim: int) -> None:
    op.execute("DROP INDEX IF EXISTS ix_requirement_embedding_hnsw")
    for t in TABLES:
        op.execute(f"UPDATE {t} SET embedding = NULL")
        op.execute(f"ALTER TABLE {t} ALTER COLUMN embedding TYPE vector({dim})")
    for t in ("requirement", "ui_state"):
        op.execute(f"UPDATE {t} SET embedding_model = NULL")
    op.execute(
        "CREATE INDEX ix_requirement_embedding_hnsw ON requirement "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def upgrade() -> None:
    _retype(1536)


def downgrade() -> None:
    _retype(1024)
