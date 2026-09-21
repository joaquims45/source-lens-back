from dataclasses import dataclass, field

from tree_sitter import Node, Tree


@dataclass
class ExtractedSymbol:
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    signature: str
    parent_index: int | None
    exported: bool
    node: Node = field(repr=False)


@dataclass(frozen=True)
class ExtractedImport:
    module: str
    content: str
    start_line: int


@dataclass
class ParsedFile:
    status: str
    symbols: list[ExtractedSymbol] = field(default_factory=list)
    imports: list[ExtractedImport] = field(default_factory=list)
    errors: list[dict[str, int | str]] = field(default_factory=list)
    tree: Tree | None = field(default=None, repr=False)
