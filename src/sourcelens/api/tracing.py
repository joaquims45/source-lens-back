from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from sourcelens.api.deps import get_db
from sourcelens.api.schemas import SymbolTraceResponse, TraceEdgeResponse, TraceSymbolResponse
from sourcelens.tracing.service import SymbolTrace, TraceEdge, get_symbol_trace

router = APIRouter(prefix="/api/v1")


def _trace_symbol(edge: TraceEdge) -> TraceSymbolResponse:
    return TraceSymbolResponse(
        id=edge.symbol.id,
        path=edge.path,
        qualified_name=edge.symbol.qualified_name,
        kind=edge.symbol.kind,
        start_line=edge.symbol.start_line,
        end_line=edge.symbol.end_line,
        signature=edge.symbol.signature,
    )


def _trace_edge(edge: TraceEdge) -> TraceEdgeResponse:
    return TraceEdgeResponse(
        symbol=_trace_symbol(edge),
        line=edge.line,
        resolution=edge.resolution,
        confidence=edge.confidence,
        candidates=edge.candidates,
    )


@router.get("/analyses/{analysis_id}/symbols/{symbol_id}/trace")
def trace_symbol(
    analysis_id: UUID, symbol_id: UUID, db: Session = Depends(get_db)
) -> SymbolTraceResponse:
    trace: SymbolTrace = get_symbol_trace(db, analysis_id, symbol_id)
    return SymbolTraceResponse(
        symbol=TraceSymbolResponse(
            id=trace.symbol.id,
            path=trace.path,
            qualified_name=trace.symbol.qualified_name,
            kind=trace.symbol.kind,
            start_line=trace.symbol.start_line,
            end_line=trace.symbol.end_line,
            signature=trace.symbol.signature,
        ),
        callers=[_trace_edge(e) for e in trace.callers],
        callees=[_trace_edge(e) for e in trace.callees],
    )
