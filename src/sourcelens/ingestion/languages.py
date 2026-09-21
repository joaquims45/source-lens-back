from collections import Counter
from pathlib import PurePosixPath

from sourcelens.ingestion.discovery import DiscoveredFile

EXTENSIONS = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".toml": "toml",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".sh": "shell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
}
SUPPORTED = {"python", "javascript", "typescript", "tsx"}


def detect(path: str, content: str) -> str:
    file = PurePosixPath(path)
    if file.name.lower().startswith("dockerfile"):
        return "dockerfile"
    language = EXTENSIONS.get(file.suffix.lower())
    if language:
        return language
    first = content.partition("\n")[0]
    if first.startswith("#!"):
        if "python" in first:
            return "python"
        if "node" in first:
            return "javascript"
    return "text"


def statistics(files: list[DiscoveredFile]) -> dict[str, dict[str, int]]:
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    for file in files:
        language = detect(file.path, file.content)
        counts[language] += 1
        sizes[language] += file.size
    return {
        language: {"files": counts[language], "bytes": sizes[language]}
        for language in sorted(counts)
    }
