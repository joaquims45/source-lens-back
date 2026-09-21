from alembic import context

from sourcelens.persistence.database import engine
from sourcelens.persistence.models import Base

with engine().connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
