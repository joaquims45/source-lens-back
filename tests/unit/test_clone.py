import subprocess
from unittest.mock import patch

import pytest

from sourcelens.api.errors import DomainError
from sourcelens.config import Settings
from sourcelens.ingestion.clone import directory_bytes, run_git


def test_git_failure_is_sanitized_and_env_isolated(tmp_path):
    settings = Settings()
    real = subprocess.Popen
    commands = []

    def spawn(command, **kwargs):
        commands.append((command, kwargs["env"]))
        return real(["git", "not-a-real-command"], **kwargs)

    with patch("sourcelens.ingestion.clone.subprocess.Popen", spawn):
        with pytest.raises(DomainError, match="clone failed"):
            run_git(["clone", "--", "https://github.com/a/b"], tmp_path, settings, lambda: None)
    assert "protocol.allow=never" in commands[0][0]
    assert commands[0][1]["GIT_TERMINAL_PROMPT"] == "0"
    assert "DATABASE_URL" not in commands[0][1]


def test_size_count(tmp_path):
    (tmp_path / "file").write_bytes(b"123")
    assert directory_bytes(tmp_path) == 3
