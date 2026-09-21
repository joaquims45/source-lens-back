from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from sourcelens.api.deps import get_db
from sourcelens.api.schemas import (
    ArchitectureComponentResponse,
    ArchitectureEdgeResponse,
    ArchitectureGraphResponse,
    ArchitectureNodeResponse,
    EvidenceResponse,
)
from sourcelens.architecture.graph import (
    ArchitectureGraph,
    Edge,
    Evidence,
    Node,
    component,
    dependencies,
)
from sourcelens.architecture.service import get_cached_architecture_graph

router = APIRouter(prefix="/api/v1")


def _evidence(evidence: list[Evidence]) -> list[EvidenceResponse]:
    return [
        EvidenceResponse(file=item.file, line=item.line, reason=item.reason, origin=item.origin)
        for item in evidence
    ]


def _node(node: Node) -> ArchitectureNodeResponse:
    return ArchitectureNodeResponse(
        id=node.id,
        label=node.label,
        type=node.type,
        confidence=node.confidence,
        source_files=node.source_files,
        metadata=node.metadata,
        evidence=_evidence(node.evidence),
    )


def _edge(edge: Edge) -> ArchitectureEdgeResponse:
    return ArchitectureEdgeResponse(
        source=edge.source,
        target=edge.target,
        type=edge.type,
        confidence=edge.confidence,
        evidence=_evidence(edge.evidence),
    )


def _graph_response(graph: ArchitectureGraph) -> ArchitectureGraphResponse:
    return ArchitectureGraphResponse(
        name=graph.name,
        schema_version=graph.schema_version,
        nodes=[_node(node) for node in graph.nodes],
        edges=[_edge(edge) for edge in graph.edges],
        warnings=graph.warnings,
    )


@router.get("/analyses/{analysis_id}/architecture")
def get_architecture(analysis_id: UUID, db: Session = Depends(get_db)) -> ArchitectureGraphResponse:
    return _graph_response(get_cached_architecture_graph(db, analysis_id))


@router.get("/analyses/{analysis_id}/architecture/components/{component_id}")
def get_component(
    analysis_id: UUID, component_id: str, db: Session = Depends(get_db)
) -> ArchitectureComponentResponse:
    graph = get_cached_architecture_graph(db, analysis_id)
    node = component(graph, component_id)
    return ArchitectureComponentResponse(
        node=_node(node),
        incoming=[_edge(e) for e in dependencies(graph, component_id, "incoming")],
        outgoing=[_edge(e) for e in dependencies(graph, component_id, "outgoing")],
    )
