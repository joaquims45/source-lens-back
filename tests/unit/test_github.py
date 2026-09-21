import pytest

from sourcelens.api.errors import DomainError
from sourcelens.ingestion.github import validate_ref, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://github.com.evil/a/b",
        "https://x@github.com/a/b",
        "https://github.com:443/a/b",
        "https://github.com/a/b?x=1",
        "https://github.com/a/b#x",
        "https://github.com/a/b/tree/main",
        "https://github.com/a/%2e%2e",
        "https://github.com/a/..",
        "https://github.com/a/b\n",
        "file:///etc/passwd",
        "https://127.0.0.1/a/b",
    ],
)
def test_rejects_non_repository_urls(url):
    with pytest.raises(DomainError):
        validate_url(url)


def test_normalizes_identity():
    assert validate_url("https://github.com/Owner/Repo.git/").url == "https://github.com/owner/repo"
    assert validate_ref("feature/auth") == "feature/auth"


@pytest.mark.parametrize("ref", ["--upload-pack=evil", "a..b", "a.lock", "a@{0}", "a b"])
def test_rejects_unsafe_refs(ref):
    with pytest.raises(DomainError):
        validate_ref(ref)
