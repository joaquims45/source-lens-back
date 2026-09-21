import json
import time
from collections.abc import Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from sourcelens.api.deps import get_db
from sourcelens.api.errors import DomainError
from sourcelens.api.schemas import (
    AnalysisResponse,
    FileDetail,
    FileSummary,
    ImportSummary,
    JobSummary,
    RepositorySummary,
    StageSummary,
    SubmitRepositoryRequest,
    SubmitRepositoryResponse,
    SymbolSummary,
)
from sourcelens.ingestion.service import submit
from sourcelens.jobs.state import TERMINAL
from sourcelens.persistence.database import session as open_session
from sourcelens.persistence.models import (
    Analysis,
    Event,
    Import,
    Job,
    Repository,
    SourceFile,
    Stage,
    Symbol,
)

router = APIRouter(prefix="/api/v1")


def _analysis_or_404(db: Session, analysis_id: UUID) -> Analysis:
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise DomainError("analysis_not_found", "Analysis not found", 404)
    return analysis


def _symbol_summary(symbol: Symbol, file_path: str) -> SymbolSummary:
    return SymbolSummary(
        id=symbol.id,
        file_id=symbol.file_id,
        file_path=file_path,
        parent_id=symbol.parent_id,
        name=symbol.name,
        qualified_name=symbol.qualified_name,
        kind=symbol.kind,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        signature=symbol.signature,
        exported=bool(symbol.details.get("exported")),
    )


@router.post("/repositories", status_code=202)
def submit_repository(
    body: SubmitRepositoryRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> SubmitRepositoryResponse:
    job = submit(db, body.url, body.ref, idempotency_key, request.state.request_id)
    analysis = db.get(Analysis, job.analysis_id)
    assert analysis is not None
    return SubmitRepositoryResponse(
        repository_id=analysis.repository_id,
        analysis_id=job.analysis_id,
        job_id=job.id,
        status=job.status,
    )


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: UUID, db: Session = Depends(get_db)) -> AnalysisResponse:
    analysis = _analysis_or_404(db, analysis_id)
    repository = db.get(Repository, analysis.repository_id)
    assert repository is not None
    job = db.scalar(select(Job).where(Job.analysis_id == analysis_id))
    stages = (
        db.scalars(select(Stage).where(Stage.job_id == job.id).order_by(Stage.name)).all()
        if job
        else []
    )
    return AnalysisResponse(
        id=analysis.id,
        repository=RepositorySummary(
            id=repository.id,
            owner=repository.owner,
            name=repository.name,
            url=repository.canonical_url,
        ),
        requested_ref=analysis.requested_ref,
        commit_sha=analysis.commit_sha,
        status=analysis.status,
        pipeline_version=analysis.pipeline_version,
        versions=analysis.versions,
        capabilities=analysis.capabilities,
        stats=analysis.stats,
        created_at=analysis.created_at,
        completed_at=analysis.completed_at,
        job=JobSummary(status=job.status, attempt=job.attempt, error=job.error) if job else None,
        stages=[
            StageSummary(
                name=stage.name,
                status=stage.status,
                processed=stage.processed,
                total=stage.total,
                unit=stage.unit,
                details=stage.details,
            )
            for stage in stages
        ],
    )


@router.get("/analyses/{analysis_id}/events")
def stream_events(analysis_id: UUID, since: int = Query(0, ge=0)) -> StreamingResponse:
    with open_session() as db:
        _analysis_or_404(db, analysis_id)

    def generate() -> Iterator[str]:
        cursor = since
        while True:
            with open_session() as db:
                job = db.scalar(select(Job).where(Job.analysis_id == analysis_id))
                if job is None:
                    return
                events = db.scalars(
                    select(Event)
                    .where(Event.job_id == job.id, Event.sequence > cursor)
                    .order_by(Event.sequence)
                ).all()
                job_status = job.status
            for event in events:
                cursor = event.sequence
                payload = json.dumps(event.payload)
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"
            if job_status in TERMINAL:
                return
            time.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/analyses/{analysis_id}/files")
def list_files(analysis_id: UUID, db: Session = Depends(get_db)) -> list[FileSummary]:
    _analysis_or_404(db, analysis_id)
    files = db.scalars(
        select(SourceFile).where(SourceFile.analysis_id == analysis_id).order_by(SourceFile.path)
    ).all()
    return [
        FileSummary(
            id=file.id,
            path=file.path,
            language=file.language,
            size_bytes=file.size_bytes,
            parse_status=file.parse_status,
        )
        for file in files
    ]


@router.get("/analyses/{analysis_id}/files/{file_id}")
def get_file(analysis_id: UUID, file_id: UUID, db: Session = Depends(get_db)) -> FileDetail:
    _analysis_or_404(db, analysis_id)
    file = db.scalar(
        select(SourceFile).where(
            SourceFile.analysis_id == analysis_id, SourceFile.id == file_id
        )
    )
    if file is None:
        raise DomainError("file_not_found", "File not found", 404)
    symbols = db.scalars(
        select(Symbol)
        .where(Symbol.analysis_id == analysis_id, Symbol.file_id == file_id)
        .order_by(Symbol.start_line)
    ).all()
    imports = db.scalars(
        select(Import)
        .where(Import.analysis_id == analysis_id, Import.file_id == file_id)
        .order_by(Import.start_line)
    ).all()
    return FileDetail(
        id=file.id,
        path=file.path,
        language=file.language,
        size_bytes=file.size_bytes,
        parse_status=file.parse_status,
        diagnostics=file.diagnostics,
        content=file.content,
        symbols=[_symbol_summary(symbol, file.path) for symbol in symbols],
        imports=[
            ImportSummary(
                module=imported.module,
                content=imported.content,
                start_line=imported.start_line,
            )
            for imported in imports
        ],
    )


@router.get("/analyses/{analysis_id}/symbols")
def list_symbols(
    analysis_id: UUID, q: str | None = Query(None), db: Session = Depends(get_db)
) -> list[SymbolSummary]:
    _analysis_or_404(db, analysis_id)
    statement = (
        select(Symbol, SourceFile.path)
        .join(SourceFile, Symbol.file_id == SourceFile.id)
        .where(Symbol.analysis_id == analysis_id)
    )
    if q:
        statement = statement.where(Symbol.qualified_name.ilike(f"%{q}%"))
    statement = statement.order_by(SourceFile.path, Symbol.start_line).limit(200)
    return [_symbol_summary(symbol, path) for symbol, path in db.execute(statement).all()]
