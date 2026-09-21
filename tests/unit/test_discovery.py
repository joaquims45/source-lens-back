import pytest

from sourcelens.api.errors import DomainError
from sourcelens.config import Settings
from sourcelens.ingestion.discovery import discover
from sourcelens.security.paths import safe_path


def test_exclusions_and_line_preserving_redaction(tmp_path):
    (tmp_path / ".env").write_text("secret")
    (tmp_path / "binary").write_bytes(b"\x00")
    (tmp_path / "app.py").write_bytes(b'password = "hunter2"\r\nprint("hello")\r\n')
    (tmp_path / "node_modules").mkdir()
    result = discover(tmp_path, Settings(), lambda: None)
    assert len(result.files) == 1
    assert result.files[0].content == 'password = "*******"\r\nprint("hello")\r\n'
    assert result.files[0].redactions == 1
    assert set(result.exclusions.values()) == {
        "sensitive_file",
        "binary_or_non_utf8",
        "excluded_directory",
    }


def test_limits_and_traversal(tmp_path):
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")
    with pytest.raises(DomainError, match="content limits"):
        discover(tmp_path, Settings(max_files=1), lambda: None)
    for path in ["../outside", "/etc/passwd", "C:/secret", "a\\..\\secret"]:
        with pytest.raises(DomainError):
            safe_path(tmp_path, path)
