import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import structlog

from sourcelens.api.errors import DomainError
from sourcelens.config import get_settings
from sourcelens.ingestion.clone import clone_repository
from sourcelens.ingestion.discovery import discover
from sourcelens.ingestion.github import GitHubRepository
from sourcelens.ingestion.languages import SUPPORTED, statistics
from sourcelens.intelligence.chunking import CHUNKER_VERSION
from sourcelens.intelligence.parser import parser_versions
from sourcelens.intelligence.pipeline import build_file_artifacts
from sourcelens.jobs import state
from sourcelens.jobs.celery_app import app
from sourcelens.persistence.database import session
from sourcelens.persistence.models import Analysis, Exclusion, Job, Repository

logger = structlog.get_logger()

# Renewing the lease on every heartbeat call would hit the database far more
# often than the lease actually needs (clone/discovery call it many times a
# second); this bounds it to a fraction of the lease lifetime instead.
HEARTBEAT_INTERVAL_SECONDS = 5.0


def make_heartbeat(job_id: UUID, owner: UUID) -> Callable[[], None]:
    last = 0.0

    def heartbeat() -> None:
        nonlocal last
        elapsed = time.monotonic()
        if elapsed - last < HEARTBEAT_INTERVAL_SECONDS:
            return
        last = elapsed
        with session() as db, db.begin():
            state.owned(db, job_id, owner)

    return heartbeat


def report(
    job_id: UUID,
    owner: UUID,
    name: str,
    status: str,
    processed: int = 0,
    total: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    with session() as db, db.begin():
        state.progress(db, job_id, owner, name, status, processed, total, details=details)


@app.task(name="sourcelens.ingest")  # type: ignore[untyped-decorator]
def ingest(job_id: str) -> None:
    job_uuid = UUID(job_id)
    with session() as db, db.begin():
        owner = state.claim(db, job_uuid)
        if owner is None:
            return
        job = db.get(Job, job_uuid)
        assert job is not None
        analysis = db.get(Analysis, job.analysis_id)
        assert analysis is not None
        repository = db.get(Repository, analysis.repository_id)
        assert repository is not None
        analysis_id, requested_ref = analysis.id, analysis.requested_ref
        repo = GitHubRepository(repository.owner, repository.name)

    try:
        run(job_uuid, owner, analysis_id, repo, requested_ref)
    except DomainError as exc:
        logger.warning("ingest_domain_error", job_id=job_id, code=exc.code)
        with session() as db, db.begin():
            state.fail(db, job_uuid, owner, exc.code)
    except Exception:
        logger.exception("ingest_unexpected_error", job_id=job_id)
        with session() as db, db.begin():
            state.fail(db, job_uuid, owner, "internal_error")


def run(
    job_id: UUID,
    owner: UUID,
    analysis_id: UUID,
    repo: GitHubRepository,
    requested_ref: str | None,
) -> None:
    settings = get_settings()
    heartbeat = make_heartbeat(job_id, owner)

    report(job_id, owner, "clone", "started")
    with clone_repository(repo, requested_ref, settings, heartbeat) as (root, commit_sha):
        report(job_id, owner, "clone", "completed", details={"commit_sha": commit_sha})
        with session() as db, db.begin():
            analysis = db.get(Analysis, analysis_id)
            assert analysis is not None
            analysis.commit_sha = commit_sha

        report(job_id, owner, "discovery", "started")
        discovery = discover(root, settings, heartbeat)
        report(
            job_id,
            owner,
            "discovery",
            "completed",
            processed=len(discovery.files),
            total=len(discovery.files),
            details={"excluded": len(discovery.exclusions)},
        )

        language_stats = statistics(discovery.files)
        report(job_id, owner, "languages", "completed", details={"languages": language_stats})

        report(job_id, owner, "parsing", "started", total=len(discovery.files))
        symbol_count = chunk_count = 0
        with session() as db, db.begin():
            for path, reason in discovery.exclusions.items():
                db.add(Exclusion(analysis_id=analysis_id, path=path, reason=reason))
            for index, discovered in enumerate(discovery.files, start=1):
                file_row, symbols, imports, chunks = build_file_artifacts(analysis_id, discovered)
                # Symbols/chunks reference file_id (and chunks reference symbol_id) via
                # real foreign keys with no ORM relationship() to drive flush ordering,
                # so each dependency tier must be flushed before the next is added.
                db.add(file_row)
                db.flush()
                db.add_all(symbols)
                db.add_all(imports)
                db.flush()
                db.add_all(chunks)
                symbol_count += len(symbols)
                chunk_count += len(chunks)
                if index % 25 == 0:
                    heartbeat()
        report(
            job_id,
            owner,
            "parsing",
            "completed",
            processed=len(discovery.files),
            total=len(discovery.files),
            details={"symbols": symbol_count, "chunks": chunk_count},
        )

    with session() as db, db.begin():
        state.complete(
            db,
            job_id,
            owner,
            {
                "versions": {"parser": parser_versions(), "chunker": CHUNKER_VERSION},
                "capabilities": {"languages": sorted(SUPPORTED)},
                "stats": {
                    "files": len(discovery.files),
                    "excluded": len(discovery.exclusions),
                    "symbols": symbol_count,
                    "chunks": chunk_count,
                    "languages": language_stats,
                },
            },
        )
