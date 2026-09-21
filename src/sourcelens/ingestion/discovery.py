import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sourcelens.api.errors import DomainError
from sourcelens.config import Settings
from sourcelens.security.paths import safe_path
from sourcelens.security.secrets import redact

IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "vendor",
    "dist",
    "build",
    "__pycache__",
    ".next",
    "coverage",
}
IGNORED_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock"}


@dataclass(frozen=True)
class DiscoveredFile:
    path: str
    content: str
    size: int
    redactions: int


@dataclass
class Discovery:
    files: list[DiscoveredFile] = field(default_factory=list)
    exclusions: dict[str, str] = field(default_factory=dict)
    examined: int = 0
    admitted_bytes: int = 0


def exclusion_reason(name: str) -> str | None:
    name = name.lower()
    if (
        name == ".env"
        or name.startswith(".env.")
        or name.endswith(".env")
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
        or name in {"id_rsa", "id_ed25519", "credentials", ".npmrc", ".netrc"}
    ):
        return "sensitive_file"
    if name in IGNORED_FILES or name.endswith((".min.js", ".map")):
        return "generated_file"
    return None


def discover(root: Path, settings: Settings, heartbeat: Callable[[], None]) -> Discovery:
    result = Discovery()
    directories = [root]
    while directories:
        directory = directories.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                result.examined += 1
                if result.examined > settings.max_entries:
                    raise DomainError("entry_limit", "Repository contains too many entries")
                if result.examined % 100 == 0:
                    heartbeat()
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if entry.is_symlink():
                    result.exclusions[relative] = "symlink"
                    continue
                safe_path(root, relative)
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() in IGNORED_DIRS:
                        result.exclusions[relative] = "excluded_directory"
                    else:
                        directories.append(path)
                    continue
                reason = exclusion_reason(entry.name)
                if not entry.is_file(follow_symlinks=False):
                    reason = "non_regular_file"
                elif entry.stat(follow_symlinks=False).st_size > settings.max_file_bytes:
                    reason = "file_too_large"
                if reason:
                    result.exclusions[relative] = reason
                    continue
                with path.open("rb") as stream:
                    raw = stream.read(settings.max_file_bytes + 1)
                if len(raw) > settings.max_file_bytes:
                    result.exclusions[relative] = "file_too_large"
                    continue
                try:
                    if b"\x00" in raw:
                        raise UnicodeError
                    content = raw.decode("utf-8-sig")
                    if any(ord(char) < 9 or 13 < ord(char) < 32 for char in content):
                        raise UnicodeError
                except UnicodeError:
                    result.exclusions[relative] = "binary_or_non_utf8"
                    continue
                result.admitted_bytes += len(raw)
                if (
                    len(result.files) >= settings.max_files
                    or result.admitted_bytes > settings.max_text_bytes
                ):
                    raise DomainError("content_limit", "Repository exceeds admitted content limits")
                content, count = redact(content)
                result.files.append(DiscoveredFile(relative, content, len(raw), count))
    result.files.sort(key=lambda file: file.path)
    return result
