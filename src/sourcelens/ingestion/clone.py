import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from sourcelens.api.errors import DomainError
from sourcelens.config import Settings
from sourcelens.ingestion.github import GitHubRepository, validate_ref


def directory_bytes(root: Path) -> int:
    total = 0
    for directory, _, files in os.walk(root, followlinks=False):
        for filename in files:
            try:
                total += (Path(directory) / filename).lstat().st_size
            except FileNotFoundError:
                continue
    return total


def stop(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def run_git(args: list[str], root: Path, settings: Settings, heartbeat: Callable[[], None]) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SystemRoot", "WINDIR", "TMP", "TEMP", "LANG"}
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_LFS_SKIP_SMUDGE="1",
    )
    command = [
        "git",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        "-c",
        "http.followRedirects=false",
        "-c",
        "credential.helper=",
        "-c",
        f"core.hooksPath={root / 'no-hooks'}",
        *args,
    ]
    started = time.monotonic()
    with subprocess.Popen(
        command,
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
    ) as process:
        try:
            while process.poll() is None:
                heartbeat()
                if time.monotonic() - started > settings.clone_timeout:
                    raise DomainError("clone_timeout", "Clone exceeded its time limit")
                if directory_bytes(root) > settings.max_clone_bytes:
                    raise DomainError("repository_too_large", "Clone exceeded its storage limit")
                time.sleep(0.2)
            if process.returncode:
                raise DomainError("clone_failed", "Repository or ref unavailable; clone failed")
            if directory_bytes(root) > settings.max_clone_bytes:
                raise DomainError("repository_too_large", "Clone exceeded its storage limit")
        except BaseException:
            if process.poll() is None:
                stop(process)
            raise


@contextmanager
def clone_repository(
    repo: GitHubRepository,
    ref: str | None,
    settings: Settings,
    heartbeat: Callable[[], None],
    pinned_sha: str | None = None,
) -> Iterator[tuple[Path, str]]:
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="sourcelens-", dir=settings.workspace_dir) as temporary:
        root = Path(temporary).resolve()
        checkout = root / "repository"
        args = ["clone", "--depth=1", "--no-tags", "--no-checkout"]
        if ref:
            args += ["--branch", str(validate_ref(ref))]
        run_git([*args, "--", repo.url, str(checkout)], root, settings, heartbeat)
        if pinned_sha:
            run_git(
                ["-C", str(checkout), "fetch", "--depth=1", "origin", pinned_sha],
                root,
                settings,
                heartbeat,
            )
        target = pinned_sha or "HEAD"
        run_git(
            ["-C", str(checkout), "-c", "core.symlinks=true", "checkout", "--detach", target],
            root,
            settings,
            heartbeat,
        )
        sha = (
            subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], timeout=5)
            .decode()
            .strip()
        )
        yield checkout, sha
