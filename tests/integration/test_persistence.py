from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from sourcelens.persistence.models import Analysis, Repository, SourceFile, Symbol


@pytest.mark.integration
def test_symbols_cannot_cross_snapshot_boundaries(db):
    repo = Repository(canonical_url=f"https://github.com/test/{uuid4()}", owner="test", name="a")
    db.add(repo)
    db.flush()
    first, second = Analysis(repository_id=repo.id), Analysis(repository_id=repo.id)
    db.add_all([first, second])
    db.flush()
    file = SourceFile(
        analysis_id=first.id,
        path="a.py",
        language="python",
        size_bytes=1,
        content_hash="a",
        content="x",
        parse_status="ok",
    )
    db.add(file)
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(
            Symbol(
                analysis_id=second.id,
                file_id=file.id,
                name="x",
                qualified_name="x",
                kind="function",
                start_line=1,
                end_line=1,
                start_byte=0,
                end_byte=1,
                signature="x",
            )
        )
        db.flush()
