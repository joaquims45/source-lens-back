from typing import Any

from alembic import context

from sourcelens.persistence.database import engine
from sourcelens.persistence.models import Base


def include_object(object_: Any, name: str, type_: str, reflected: bool, compare_to: Any) -> bool:
    # code_chunks.content_tsv is a database-generated column with no ORM
    # attribute by design (see the `add chunk embeddings` migration and
    # sourcelens.retrieval.lexical), so autogenerate must not propose
    # dropping it just because the model doesn't mention it.
    if type_ == "column" and name == "content_tsv":
        return False
    if type_ == "index" and name == "ix_code_chunks_content_tsv":
        return False
    return True


with engine().connect() as connection:
    context.configure(
        connection=connection, target_metadata=Base.metadata, include_object=include_object
    )
    with context.begin_transaction():
        context.run_migrations()
