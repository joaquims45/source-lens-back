import pytest

from sourcelens.api.errors import DomainError
from sourcelens.architecture.graph import Evidence, GraphBuilder, dependencies, trace


def evidence(file: str = "src/a.ts", line: int = 1, reason: str = "import") -> list[Evidence]:
    return [Evidence(file=file, line=line, reason=reason)]


def test_requires_evidence_and_deduplicates_repeated_claims():
    builder = GraphBuilder("test")
    for node_id in ("a", "b", "c"):
        builder.node(node_id, node_id, "module", evidence())
    builder.node("a", "a", "module", evidence())  # repeated claim, same evidence

    graph = builder.build()

    assert graph.nodes[0].evidence == evidence()


def test_merging_a_node_unions_evidence_and_keeps_max_confidence():
    builder = GraphBuilder("test")
    builder.node("a", "a", "service", evidence(reason="name pattern"), confidence=0.6)
    builder.node("a", "a", "service", evidence(reason="decorator"), confidence=0.9)

    graph = builder.build()

    reasons = {item.reason for item in graph.nodes[0].evidence}
    assert reasons == {"name pattern", "decorator"}
    assert graph.nodes[0].confidence == 0.9


def test_node_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="evidence"):
        GraphBuilder("bad").node("x", "x", "module", [])


def test_evidence_rejects_non_relative_paths():
    with pytest.raises(ValueError, match="repository-relative"):
        Evidence(file="/etc/passwd", line=1, reason="x")
    with pytest.raises(ValueError, match="repository-relative"):
        Evidence(file="../secret", line=1, reason="x")


def test_self_loop_edges_are_ignored():
    builder = GraphBuilder("test")
    builder.node("a", "a", "module", evidence())
    builder.edge("a", "a", "imports", evidence())

    assert builder.build().edges == []


def test_dependencies_and_trace_find_a_path_and_reject_cycles_back():
    builder = GraphBuilder("test")
    for node_id in ("a", "b", "c"):
        builder.node(node_id, node_id, "module", evidence())
    builder.edge("a", "b", "imports", evidence())
    builder.edge("b", "a", "imports", evidence())
    builder.edge("b", "c", "imports", evidence())
    graph = builder.build()

    assert len(dependencies(graph, "a")) == 1
    path = trace(graph, "a", "c")
    assert path is not None and len(path) == 2
    assert trace(graph, "c", "a") is None


def test_component_lookup_raises_a_404_domain_error_when_missing():
    builder = GraphBuilder("test")
    builder.node("a", "a", "module", evidence())
    graph = builder.build()

    with pytest.raises(DomainError, match="not found"):
        dependencies(graph, "missing")
