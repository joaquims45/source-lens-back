from uuid import uuid4

from sourcelens.ingestion.discovery import DiscoveredFile
from sourcelens.intelligence.pipeline import build_file_artifacts


def test_python_file_produces_linked_symbols_imports_and_chunks():
    content = "from pathlib import Path\n\nclass Service:\n    def run(self):\n        return 1\n"
    discovered = DiscoveredFile("src/service.py", content, len(content.encode()), redactions=0)
    file_row, symbols, imports, chunks = build_file_artifacts(uuid4(), discovered)

    assert file_row.language == "python"
    assert file_row.parse_status == "ok"
    assert [symbol.qualified_name for symbol in symbols] == ["Service", "Service.run"]
    method = symbols[1]
    assert method.parent_id == symbols[0].id
    assert imports[0].module == "pathlib"
    assert chunks and all(chunk.file_id == file_row.id for chunk in chunks)
    method_chunk = next(chunk for chunk in chunks if chunk.kind == "method")
    assert method_chunk.symbol_id == method.id


def test_unsupported_language_is_stored_without_parsing():
    discovered = DiscoveredFile("README.md", "hello", 5, redactions=0)
    file_row, symbols, imports, chunks = build_file_artifacts(uuid4(), discovered)

    assert file_row.language == "markdown"
    assert file_row.parse_status == "unsupported"
    assert symbols == imports == chunks == []


def test_redactions_and_parse_errors_are_recorded_as_diagnostics():
    discovered = DiscoveredFile("a.py", "def broken(\n", 12, redactions=2)
    file_row, _, _, _ = build_file_artifacts(uuid4(), discovered)

    assert file_row.parse_status == "partial"
    assert file_row.diagnostics["redactions"] == 2
    assert file_row.diagnostics["errors"]
