"""add chunk embeddings"""

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision = "a59e1b18bedb"
down_revision = "f24d17861c7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "code_chunks", sa.Column("embedding", pgvector.sqlalchemy.Vector(256), nullable=True)
    )
    op.add_column("code_chunks", sa.Column("embedding_model", sa.String(), nullable=True))
    # Full-text search is a real database column (not computed on the fly) so
    # lexical retrieval can be indexed with GIN and ranked with ts_rank_cd.
    op.execute(
        "ALTER TABLE code_chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.execute("CREATE INDEX ix_code_chunks_content_tsv ON code_chunks USING GIN (content_tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_code_chunks_content_tsv")
    op.execute("ALTER TABLE code_chunks DROP COLUMN content_tsv")
    op.drop_column("code_chunks", "embedding_model")
    op.drop_column("code_chunks", "embedding")
