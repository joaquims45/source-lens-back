from sourcelens.ingestion.discovery import DiscoveredFile
from sourcelens.ingestion.languages import detect, statistics


def test_languages_and_denominator():
    assert detect("component.tsx", "") == "tsx"
    assert detect("script", "#!/usr/bin/env python3\n") == "python"
    assert detect("other.xyz", "") == "text"
    assert statistics([DiscoveredFile("a.py", "x", 1, 0), DiscoveredFile("b.ts", "ab", 2, 0)]) == {
        "python": {"files": 1, "bytes": 1},
        "typescript": {"files": 1, "bytes": 2},
    }
