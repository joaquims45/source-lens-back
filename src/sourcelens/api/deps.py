from collections.abc import Iterator

from sqlalchemy.orm import Session

from sourcelens.persistence.database import session as open_session


def get_db() -> Iterator[Session]:
    with open_session() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
