from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from sourcelens.api.errors import DomainError
from sourcelens.architecture.detect import (
    detect_component_usage_edges,
    detect_components,
    detect_infrastructure,
    detect_module_imports,
    detect_modules,
)
from sourcelens.architecture.graph import ArchitectureGraph, Evidence, GraphBuilder
from sourcelens.cache import cache_get, cache_set
from sourcelens.config import Settings, get_settings
from sourcelens.persistence.models import Analysis, Import, Repository, SourceFile, Symbol


def build_architecture_graph(db: Session, analysis_id: UUID) -> ArchitectureGraph:
    """Computed on demand from already-persisted M1 facts (files, symbols,
    imports) rather than stored during ingestion: the graph is a pure,
    deterministic view over immutable per-analysis data, so detector changes
    apply retroactively to any past analysis without re-ingesting it.
    """
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise DomainError("analysis_not_found", "Analysis not found", 404)
    repository = db.get(Repository, analysis.repository_id)
    assert repository is not None

    files = list(db.scalars(select(SourceFile).where(SourceFile.analysis_id == analysis_id)))
    imports = list(db.scalars(select(Import).where(Import.analysis_id == analysis_id)))
    symbol_rows: list[tuple[Symbol, str]] = [
        (symbol, path)
        for symbol, path in db.execute(
            select(Symbol, SourceFile.path)
            .join(SourceFile, Symbol.file_id == SourceFile.id)
            .where(Symbol.analysis_id == analysis_id)
        ).all()
    ]

    files_by_id = {file.id: file for file in files}
    content_by_file = {file.id: file.content for file in files}

    builder = GraphBuilder(f"{repository.owner}/{repository.name}")

    root_markers = ("README.md", "pyproject.toml", "package.json", "Dockerfile")
    paths = [f.path for f in files]
    entry_file = next((p for p in root_markers if p in paths), None) or (
        paths[0] if paths else "README.md"
    )
    builder.node(
        "application:root",
        f"{repository.owner}/{repository.name}",
        "application",
        [Evidence(file=entry_file, line=1, reason="repository root")],
        confidence=1.0,
    )

    module_id_by_name = detect_modules(builder, files)
    detect_module_imports(builder, files_by_id, imports, module_id_by_name)
    detect_infrastructure(builder, files)
    component_ids = detect_components(builder, symbol_rows, content_by_file)
    detect_component_usage_edges(builder, symbol_rows, content_by_file, component_ids)

    return builder.build()


def get_cached_architecture_graph(
    db: Session, analysis_id: UUID, settings: Settings | None = None
) -> ArchitectureGraph:
    """Same result as `build_architecture_graph`, cached in Redis. Only a
    *completed* analysis is cached — its files/symbols/imports are immutable
    from that point on, so the cache never needs invalidation, just a TTL as
    a memory safety valve. A still-running analysis is never cached, since
    caching a graph built from partial data would be wrong.
    """
    settings = settings or get_settings()
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise DomainError("analysis_not_found", "Analysis not found", 404)
    if analysis.status != "completed":
        return build_architecture_graph(db, analysis_id)

    key = f"architecture:{analysis_id}"
    cached: ArchitectureGraph | None = cache_get(settings, key)
    if cached is not None:
        return cached
    graph = build_architecture_graph(db, analysis_id)
    cache_set(settings, key, graph, settings.architecture_cache_ttl_seconds)
    return graph
