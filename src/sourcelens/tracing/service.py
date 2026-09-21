from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from sourcelens.api.errors import DomainError
from sourcelens.persistence.models import Analysis, SourceFile, Symbol
from sourcelens.tracing.calls import Resolution, detect_calls


@dataclass(frozen=True)
class TraceEdge:
    symbol: Symbol
    path: str
    line: int
    resolution: Resolution
    confidence: float
    candidates: list[UUID]


@dataclass(frozen=True)
class SymbolTrace:
    symbol: Symbol
    path: str
    callers: list[TraceEdge]
    callees: list[TraceEdge]


def get_symbol_trace(db: Session, analysis_id: UUID, symbol_id: UUID) -> SymbolTrace:
    """Computed on demand, like the architecture graph: the whole analysis's
    call graph is derived fresh from persisted symbols/content rather than
    stored, so detector changes apply retroactively without re-ingesting.
    """
    if db.get(Analysis, analysis_id) is None:
        raise DomainError("analysis_not_found", "Analysis not found", 404)

    symbol_rows: list[tuple[Symbol, str]] = [
        (symbol, path)
        for symbol, path in db.execute(
            select(Symbol, SourceFile.path)
            .join(SourceFile, Symbol.file_id == SourceFile.id)
            .where(Symbol.analysis_id == analysis_id)
        ).all()
    ]
    symbol_by_id = {symbol.id: symbol for symbol, _ in symbol_rows}
    path_by_id = {symbol.id: path for symbol, path in symbol_rows}

    if symbol_id not in symbol_by_id:
        raise DomainError("symbol_not_found", "Symbol not found", 404)

    files = list(db.scalars(select(SourceFile).where(SourceFile.analysis_id == analysis_id)))
    content_by_file = {file.id: file.content for file in files}

    edges = detect_calls([symbol for symbol, _ in symbol_rows], content_by_file)

    callees = [
        TraceEdge(
            symbol_by_id[e.callee_id],
            path_by_id[e.callee_id],
            e.line,
            e.resolution,
            e.confidence,
            e.candidates,
        )
        for e in edges
        if e.caller_id == symbol_id
    ]
    callers = [
        TraceEdge(
            symbol_by_id[e.caller_id],
            path_by_id[e.caller_id],
            e.line,
            e.resolution,
            e.confidence,
            e.candidates,
        )
        for e in edges
        if e.callee_id == symbol_id
    ]

    return SymbolTrace(symbol_by_id[symbol_id], path_by_id[symbol_id], callers, callees)
