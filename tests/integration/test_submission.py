from uuid import uuid4

import pytest

from sourcelens.api.errors import DomainError
from sourcelens.ingestion.service import submit


@pytest.mark.integration
def test_idempotency_checks_payload(db):
    key = str(uuid4())
    first = submit(db, "https://github.com/a/b", None, key, "request")
    assert submit(db, "https://github.com/A/B.git", None, key, "request").id == first.id
    with pytest.raises(DomainError, match="another request"):
        submit(db, "https://github.com/a/c", None, key, "request")
