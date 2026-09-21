import re
from uuid import UUID

from sourcelens.architecture.graph import Evidence, GraphBuilder
from sourcelens.architecture.signals import (
    DECORATOR_ROLE_PATTERNS,
    IMAGE_SIGNALS,
    NAME_ROLE_PATTERNS,
    PACKAGE_SIGNALS,
    USAGE_SIGNALS,
    is_compose_file,
    is_dependency_manifest,
    is_dockerfile,
)
from sourcelens.persistence.models import Import, SourceFile, Symbol

SOURCE_ROOTS = ("src", "app", "lib")
EXCLUDED_TOP_LEVEL = {"tests", "test", "docs", "scripts", "migrations", ".github", "node_modules"}
TEST_FILENAME = re.compile(r"^test_.*\.py$|.*_test\.py$|.*\.test\.[jt]sx?$|.*\.spec\.[jt]sx?$")


def is_test_path(path: str) -> bool:
    """Excludes test code from component detection: a test function whose
    name happens to contain "jwt" or "auth" (e.g. test_creating_jwt_token)
    would otherwise be misclassified as an authentication component.
    """
    parts = path.split("/")
    if any(part in ("tests", "test") for part in parts[:-1]):
        return True
    return bool(TEST_FILENAME.match(parts[-1]))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def infra_node_id(node_type: str, label: str) -> str:
    return f"infra:{node_type}:{slug(label)}"


def module_name_for_path(path: str) -> str | None:
    """Top-level source directory a file belongs to: the segment right after
    a conventional source root (src/, app/, lib/), or the file's own top
    segment otherwise. Test/docs/tooling directories aren't modules.
    """
    parts = path.split("/")
    if parts[0] in EXCLUDED_TOP_LEVEL:
        return None
    if parts[0] in SOURCE_ROOTS and len(parts) >= 3:
        return parts[1]
    if len(parts) >= 2:
        return parts[0]
    return None


def detect_modules(builder: GraphBuilder, files: list[SourceFile]) -> dict[str, str]:
    module_id_by_name: dict[str, str] = {}
    for file in files:
        module_name = module_name_for_path(file.path)
        if module_name is None:
            continue
        node_id = f"module:{slug(module_name)}"
        builder.node(
            node_id,
            module_name,
            "module",
            [Evidence(file=file.path, line=1, reason="module directory")],
            confidence=0.8,
        )
        module_id_by_name[module_name] = node_id
    return module_id_by_name


def detect_module_imports(
    builder: GraphBuilder,
    files_by_id: dict[UUID, SourceFile],
    imports: list[Import],
    module_id_by_name: dict[str, str],
) -> None:
    """Heuristic, not a resolved import graph: an import string is checked
    for a module name as a path-like token (e.g. "orders" inside
    "src.orders.service" or "../orders/service"). Approximate on purpose —
    fully resolving imports across languages is out of scope here — so
    confidence stays low and every edge still carries the citing import
    statement as evidence.
    """
    for imp in imports:
        file = files_by_id.get(imp.file_id)
        if file is None:
            continue
        source_module = module_name_for_path(file.path)
        if source_module is None:
            continue
        source_id = module_id_by_name[source_module]
        for module_name, target_id in module_id_by_name.items():
            if module_name == source_module:
                continue
            if re.search(rf"(?:^|[./_-]){re.escape(module_name)}(?:[./_-]|$)", imp.module, re.I):
                builder.edge(
                    source_id,
                    target_id,
                    "imports",
                    [
                        Evidence(
                            file=file.path, line=imp.start_line, reason=f"imports {imp.module!r}"
                        )
                    ],
                    confidence=0.5,
                )


def detect_infrastructure(builder: GraphBuilder, files: list[SourceFile]) -> None:
    for file in files:
        if is_dockerfile(file.path):
            builder.node(
                "infra:infrastructure:docker",
                "Docker",
                "infrastructure",
                [Evidence(file=file.path, line=1, reason="Dockerfile present")],
                confidence=0.9,
            )
        if is_compose_file(file.path):
            builder.node(
                "infra:infrastructure:docker-compose",
                "Docker Compose",
                "infrastructure",
                [Evidence(file=file.path, line=1, reason="docker-compose file present")],
                confidence=0.9,
            )
            for line_no, line in enumerate(file.content.splitlines(), start=1):
                match = re.search(r"image:\s*[\"']?([^\s\"'#]+)", line)
                if not match:
                    continue
                image = match.group(1)
                for pattern, node_type, label in IMAGE_SIGNALS:
                    if pattern.search(image):
                        builder.node(
                            infra_node_id(node_type, label),
                            label,
                            node_type,
                            [
                                Evidence(
                                    file=file.path,
                                    line=line_no,
                                    reason=f"docker-compose image: {image}",
                                )
                            ],
                            confidence=0.85,
                        )
        if is_dependency_manifest(file.path):
            for line_no, line in enumerate(file.content.splitlines(), start=1):
                for pattern, node_type, label in PACKAGE_SIGNALS:
                    if pattern.search(line):
                        builder.node(
                            infra_node_id(node_type, label),
                            label,
                            node_type,
                            [
                                Evidence(
                                    file=file.path, line=line_no, reason=f"declared in {file.path}"
                                )
                            ],
                            confidence=0.6,
                        )


def _snippet(symbol: Symbol, content: str) -> str:
    return content.encode()[symbol.start_byte : symbol.end_byte].decode(errors="ignore")


def detect_components(
    builder: GraphBuilder,
    symbol_rows: list[tuple[Symbol, str]],
    content_by_file: dict[UUID, str],
) -> dict[UUID, str]:
    """Classifies classes (and top-level functions, for function-based
    routes/tasks) into controller/service/repository/worker/authentication
    roles. A decorator match (e.g. `@app.get(...)`) outranks a name-suffix
    match; symbols matching neither aren't added, so the graph reflects
    identified roles rather than every class in the repository.
    """
    component_ids: dict[UUID, str] = {}
    for symbol, path in symbol_rows:
        if is_test_path(path):
            continue
        if symbol.kind == "function" and symbol.parent_id is not None:
            continue
        if symbol.kind not in ("class", "function"):
            continue
        snippet = _snippet(symbol, content_by_file.get(symbol.file_id, ""))

        role = next((r for r in DECORATOR_ROLE_PATTERNS if r.pattern.search(snippet)), None)
        if role is None:
            role = next((r for r in NAME_ROLE_PATTERNS if r.pattern.search(symbol.name)), None)
        if role is None:
            continue

        node_id = f"component:{symbol.id}"
        builder.node(
            node_id,
            symbol.qualified_name,
            role.node_type,
            [Evidence(file=path, line=symbol.start_line, reason=role.reason)],
            confidence=role.confidence,
            metadata={"kind": symbol.kind},
        )
        component_ids[symbol.id] = node_id
    return component_ids


def detect_component_usage_edges(
    builder: GraphBuilder,
    symbol_rows: list[tuple[Symbol, str]],
    content_by_file: dict[UUID, str],
    component_ids: dict[UUID, str],
) -> None:
    for symbol, path in symbol_rows:
        source_id = component_ids.get(symbol.id)
        if source_id is None:
            continue
        snippet = _snippet(symbol, content_by_file.get(symbol.file_id, ""))
        for line_offset, line in enumerate(snippet.splitlines()):
            for usage in USAGE_SIGNALS:
                if not usage.pattern.search(line):
                    continue
                line_no = symbol.start_line + line_offset
                evidence = [Evidence(file=path, line=line_no, reason=usage.reason)]
                target_id = infra_node_id(usage.node_type, usage.label)
                builder.node(target_id, usage.label, usage.node_type, evidence, confidence=0.5)
                builder.edge(source_id, target_id, usage.edge_type, evidence, confidence=0.6)
