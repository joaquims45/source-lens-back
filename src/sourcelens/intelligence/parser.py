from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from sourcelens.intelligence.models import ExtractedImport, ExtractedSymbol, ParsedFile


@lru_cache
def grammar(language: str) -> Language | None:
    if language == "python":
        return Language(tree_sitter_python.language())
    return None


def parser_versions() -> dict[str, str]:
    return {name: version(name) for name in ["tree-sitter", "tree-sitter-python"]}


def end_line(node: Node) -> int:
    return max(node.start_point.row + 1, node.end_point.row + (node.end_point.column > 0))


def text(node: Node | None, source: bytes) -> str:
    return "" if node is None else source[node.start_byte : node.end_byte].decode("utf-8")


def parse(content: str, language: str) -> ParsedFile:
    lang = grammar(language)
    if lang is None:
        return ParsedFile("unsupported")
    source = content.encode("utf-8")
    tree = Parser(lang).parse(source)
    result = ParsedFile("partial" if tree.root_node.has_error else "ok", tree=tree)
    query = Query(lang, (Path(__file__).parent / "queries" / f"{language}.scm").read_text())
    captures = QueryCursor(query).captures(tree.root_node)
    definitions = sorted(
        captures.get("definition", []), key=lambda node: (node.start_byte, -node.end_byte)
    )
    for node in definitions:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = text(name_node, source)
        parent_index = next(
            (
                index
                for index in range(len(result.symbols) - 1, -1, -1)
                if result.symbols[index].start_byte <= node.start_byte
                and result.symbols[index].end_byte >= node.end_byte
            ),
            None,
        )
        parent = result.symbols[parent_index] if parent_index is not None else None
        kind = "class" if node.type == "class_definition" else "function"
        if kind == "function" and parent and parent.kind == "class":
            kind = "method"
        outer = node.parent if node.parent and node.parent.type == "decorated_definition" else node
        body = node.child_by_field_name("body")
        signature_end = body.start_byte if body else node.end_byte
        signature = source[node.start_byte : signature_end].decode("utf-8").strip()[:1000]
        result.symbols.append(
            ExtractedSymbol(
                name,
                f"{parent.qualified_name}.{name}" if parent else name,
                kind,
                outer.start_point.row + 1,
                end_line(outer),
                outer.start_byte,
                outer.end_byte,
                signature,
                parent_index,
                False,
                node,
            )
        )
    for node in captures.get("import", []):
        module = node.child_by_field_name("module_name") or node.child_by_field_name("name")
        result.imports.append(
            ExtractedImport(text(module, source), text(node, source), node.start_point.row + 1)
        )
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            if len(result.errors) < 100:
                result.errors.append(
                    {
                        "type": node.type,
                        "start_line": node.start_point.row + 1,
                        "end_line": end_line(node),
                    }
                )
        stack.extend(reversed(node.children))
    return result
