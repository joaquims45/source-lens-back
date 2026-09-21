from pathlib import Path, PurePosixPath

from sourcelens.api.errors import DomainError


def safe_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in relative
        or ":" in relative
        or any(ord(char) < 32 for char in relative)
    ):
        raise DomainError("invalid_path", "Path is outside the repository")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise DomainError("invalid_path", "Path is outside the repository")
    return resolved
