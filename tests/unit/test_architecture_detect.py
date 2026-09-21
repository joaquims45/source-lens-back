from uuid import uuid4

from sourcelens.architecture.detect import (
    detect_component_usage_edges,
    detect_components,
    detect_infrastructure,
    detect_module_imports,
    detect_modules,
)
from sourcelens.architecture.graph import GraphBuilder
from sourcelens.persistence.models import Import, SourceFile, Symbol


def make_file(path: str, content: str = "") -> SourceFile:
    return SourceFile(
        id=uuid4(),
        analysis_id=uuid4(),
        path=path,
        language="python",
        size_bytes=len(content),
        content_hash="h",
        content=content,
        parse_status="ok",
    )


def make_symbol(file_id, name: str, kind: str, content: str, parent_id=None) -> Symbol:
    return Symbol(
        id=uuid4(),
        analysis_id=uuid4(),
        file_id=file_id,
        parent_id=parent_id,
        name=name,
        qualified_name=name,
        kind=kind,
        start_line=1,
        end_line=content.count("\n") + 1,
        start_byte=0,
        end_byte=len(content.encode()),
        signature=name,
    )


def test_detect_modules_groups_by_source_root_directory():
    builder = GraphBuilder("test")
    files = [
        make_file("src/orders/service.py"),
        make_file("src/orders/models.py"),
        make_file("src/billing/api.py"),
    ]

    module_ids = detect_modules(builder, files)

    assert set(module_ids) == {"orders", "billing"}
    graph = builder.build()
    orders_node = next(n for n in graph.nodes if n.id == module_ids["orders"])
    assert len(orders_node.source_files) == 2


def test_detect_modules_excludes_test_and_tooling_directories():
    builder = GraphBuilder("test")
    module_ids = detect_modules(
        builder, [make_file("tests/test_orders.py"), make_file(".github/workflows/ci.yml")]
    )
    assert module_ids == {}


def test_detect_module_imports_links_modules_by_token_match():
    builder = GraphBuilder("test")
    orders_file = make_file("src/orders/service.py")
    billing_file = make_file("src/billing/api.py")
    module_ids = detect_modules(builder, [orders_file, billing_file])
    imports = [
        Import(
            analysis_id=uuid4(),
            file_id=billing_file.id,
            module="src.orders.service",
            content="",
            start_line=3,
        )
    ]

    detect_module_imports(
        builder, {orders_file.id: orders_file, billing_file.id: billing_file}, imports, module_ids
    )

    graph = builder.build()
    assert any(
        e.source == module_ids["billing"]
        and e.target == module_ids["orders"]
        and e.type == "imports"
        for e in graph.edges
    )


def test_detect_infrastructure_finds_dockerfile_and_compose_images():
    builder = GraphBuilder("test")
    compose = make_file(
        "compose.yaml",
        "services:\n"
        "  postgres:\n"
        "    image: pgvector/pgvector:pg17\n"
        "  redis:\n"
        "    image: redis:7.4-alpine\n",
    )
    files = [make_file("Dockerfile", "FROM python:3.13\n"), compose]

    detect_infrastructure(builder, files)

    graph = builder.build()
    labels = {(n.type, n.label) for n in graph.nodes}
    assert ("infrastructure", "Docker") in labels
    assert ("infrastructure", "Docker Compose") in labels
    assert ("database", "PostgreSQL") in labels
    assert ("cache", "Redis") in labels


def test_detect_infrastructure_finds_dependency_manifest_packages():
    builder = GraphBuilder("test")
    manifest = make_file("requirements.txt", "fastapi\ncelery==5.5\nboto3\n")

    detect_infrastructure(builder, [manifest])

    graph = builder.build()
    labels = {(n.type, n.label) for n in graph.nodes}
    assert ("queue", "Celery") in labels
    assert ("external_api", "AWS") in labels


def test_detect_components_classifies_by_decorator_over_name():
    file = make_file("src/orders/api.py")
    content = "@app.get('/orders')\ndef list_orders():\n    return []\n"
    symbol = make_symbol(file.id, "list_orders", "function", content)
    content_by_file = {file.id: content}

    builder = GraphBuilder("test")
    component_ids = detect_components(builder, [(symbol, file.path)], content_by_file)

    assert symbol.id in component_ids
    node = next(n for n in builder.build().nodes if n.id == component_ids[symbol.id])
    assert node.type == "controller"
    assert node.confidence == 0.9


def test_detect_components_classifies_by_name_suffix():
    file = make_file("src/orders/service.py")
    content = "class OrderService:\n    def create(self):\n        pass\n"
    symbol = make_symbol(file.id, "OrderService", "class", content)

    builder = GraphBuilder("test")
    component_ids = detect_components(builder, [(symbol, file.path)], {file.id: content})

    node = next(n for n in builder.build().nodes if n.id == component_ids[symbol.id])
    assert node.type == "service"
    assert node.confidence == 0.6


def test_detect_components_skips_unclassified_symbols():
    file = make_file("src/orders/util.py")
    content = "class Helper:\n    pass\n"
    symbol = make_symbol(file.id, "Helper", "class", content)

    builder = GraphBuilder("test")
    component_ids = detect_components(builder, [(symbol, file.path)], {file.id: content})

    assert component_ids == {}


def test_detect_component_usage_edges_links_service_to_database():
    file = make_file("src/orders/service.py")
    content = "class OrderService:\n    def create(self):\n        session.query(Order).all()\n"
    symbol = make_symbol(file.id, "OrderService", "class", content)
    content_by_file = {file.id: content}

    builder = GraphBuilder("test")
    component_ids = detect_components(builder, [(symbol, file.path)], content_by_file)
    detect_component_usage_edges(builder, [(symbol, file.path)], content_by_file, component_ids)

    graph = builder.build()
    assert any(e.type == "reads" and e.target.startswith("infra:database:") for e in graph.edges)
