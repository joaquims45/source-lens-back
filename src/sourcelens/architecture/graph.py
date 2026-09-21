from dataclasses import dataclass, field
from typing import Any, Literal

from sourcelens.api.errors import DomainError

NodeType = Literal[
    "application",
    "module",
    "controller",
    "service",
    "repository",
    "database",
    "cache",
    "queue",
    "worker",
    "external_api",
    "infrastructure",
    "authentication",
]
EdgeType = Literal[
    "imports",
    "calls",
    "depends_on",
    "reads",
    "writes",
    "publishes",
    "consumes",
    "exposes",
    "authenticates_with",
]
Origin = Literal["static", "heuristic", "llm"]

NODE_TYPES: tuple[NodeType, ...] = (
    "application",
    "module",
    "controller",
    "service",
    "repository",
    "database",
    "cache",
    "queue",
    "worker",
    "external_api",
    "infrastructure",
    "authentication",
)
EDGE_TYPES: tuple[EdgeType, ...] = (
    "imports",
    "calls",
    "depends_on",
    "reads",
    "writes",
    "publishes",
    "consumes",
    "exposes",
    "authenticates_with",
)
ORIGINS: tuple[Origin, ...] = ("static", "heuristic", "llm")


@dataclass(frozen=True)
class Evidence:
    file: str
    line: int
    reason: str
    origin: Origin = "static"

    def __post_init__(self) -> None:
        if (
            not self.file
            or self.file.startswith("/")
            or ".." in self.file
            or "\\" in self.file
            or (len(self.file) > 1 and self.file[1] == ":")
        ):
            raise ValueError("Evidence must use repository-relative paths")
        if self.line < 1:
            raise ValueError("Evidence line must be positive")
        if not self.reason:
            raise ValueError("Evidence reason is required")
        if self.origin not in ORIGINS:
            raise ValueError(f"Invalid evidence origin {self.origin!r}")


@dataclass
class Node:
    id: str
    label: str
    type: NodeType
    evidence: list[Evidence]
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("A node requires at least one piece of evidence")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        self.source_files = sorted({item.file for item in self.evidence})


@dataclass
class Edge:
    source: str
    target: str
    type: EdgeType
    evidence: list[Evidence]
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("An edge requires at least one piece of evidence")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass
class ArchitectureGraph:
    name: str
    nodes: list[Node]
    edges: list[Edge]
    warnings: list[str] = field(default_factory=list)
    schema_version: str = "1.0"


def _dedupe_evidence(evidence: list[Evidence]) -> list[Evidence]:
    seen: dict[tuple[str, int, str, str], Evidence] = {}
    for item in evidence:
        seen[(item.file, item.line, item.reason, item.origin)] = item
    return list(seen.values())


class GraphBuilder:
    """Accumulates nodes/edges from many independent detectors, merging
    repeated claims about the same component instead of duplicating them.
    Every node/edge must carry evidence — see `Evidence` — so nothing in the
    resulting graph is asserted without a static/heuristic (or later, LLM)
    reason pointing at real source lines.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._nodes: dict[str, Node] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}
        self.warnings: list[str] = []

    def node(
        self,
        node_id: str,
        label: str,
        node_type: NodeType,
        evidence: list[Evidence],
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        current = self._nodes.get(node_id)
        if current is None:
            self._nodes[node_id] = Node(
                id=node_id,
                label=label,
                type=node_type,
                evidence=_dedupe_evidence(evidence),
                confidence=confidence,
                metadata=dict(metadata or {}),
            )
        else:
            current.evidence = _dedupe_evidence([*current.evidence, *evidence])
            current.source_files = sorted({item.file for item in current.evidence})
            current.confidence = max(current.confidence, confidence)
            current.metadata.update(metadata or {})
        return node_id

    def edge(
        self,
        source: str,
        target: str,
        edge_type: EdgeType,
        evidence: list[Evidence],
        confidence: float = 1.0,
    ) -> None:
        if source == target:
            return
        key = (source, target, edge_type)
        current = self._edges.get(key)
        if current is None:
            self._edges[key] = Edge(
                source=source,
                target=target,
                type=edge_type,
                evidence=_dedupe_evidence(evidence),
                confidence=confidence,
            )
        else:
            current.evidence = _dedupe_evidence([*current.evidence, *evidence])
            current.confidence = max(current.confidence, confidence)

    def build(self) -> ArchitectureGraph:
        node_ids = set(self._nodes)
        edges = [
            edge
            for edge in self._edges.values()
            if edge.source in node_ids and edge.target in node_ids
        ]
        return ArchitectureGraph(
            name=self.name,
            nodes=sorted(self._nodes.values(), key=lambda n: n.id),
            edges=edges,
            warnings=list(self.warnings),
        )


def component(graph: ArchitectureGraph, node_id: str) -> Node:
    for node in graph.nodes:
        if node.id == node_id:
            return node
    raise DomainError("component_not_found", f"Component {node_id!r} not found", 404)


def dependencies(graph: ArchitectureGraph, node_id: str, direction: str = "outgoing") -> list[Edge]:
    component(graph, node_id)
    if direction not in ("incoming", "outgoing"):
        raise ValueError("direction must be 'incoming' or 'outgoing'")
    attr = "source" if direction == "outgoing" else "target"
    return [edge for edge in graph.edges if getattr(edge, attr) == node_id]


def trace(graph: ArchitectureGraph, source_id: str, target_id: str) -> list[Edge] | None:
    component(graph, source_id)
    component(graph, target_id)
    queue: list[tuple[str, list[Edge]]] = [(source_id, [])]
    seen = {source_id}
    while queue:
        current_id, path = queue.pop(0)
        if current_id == target_id:
            return path
        for edge in dependencies(graph, current_id):
            if edge.target not in seen:
                seen.add(edge.target)
                queue.append((edge.target, [*path, edge]))
    return None
