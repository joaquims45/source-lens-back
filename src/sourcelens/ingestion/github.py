import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from sourcelens.api.errors import DomainError


@dataclass(frozen=True)
class GitHubRepository:
    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


def validate_url(value: str) -> GitHubRepository:
    if len(value) > 300 or any(ord(char) < 33 for char in value):
        raise DomainError("invalid_repository_url", "Expected a public GitHub HTTPS repository URL")
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or parts.netloc != "github.com"
        or parts.query
        or parts.fragment
        or "\\" in value
        or "%" in value
    ):
        raise DomainError("invalid_repository_url", "Expected https://github.com/owner/repository")
    match = re.fullmatch(
        r"/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9_.-]{1,100})/?", parts.path
    )
    if match is None:
        raise DomainError(
            "invalid_repository_url", "Expected a repository, not a file or branch URL"
        )
    owner, name = match.groups()
    name = name.removesuffix(".git")
    if not name or name in {".", ".."} or owner.endswith("-"):
        raise DomainError("invalid_repository_url", "Invalid repository name")
    return GitHubRepository(owner.lower(), name.lower())


def validate_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        len(value) > 200
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", value)
        or ".." in value
        or "//" in value
        or value.endswith(("/", ".", ".lock"))
    ):
        raise DomainError("invalid_ref", "Expected a branch or tag name")
    return value
