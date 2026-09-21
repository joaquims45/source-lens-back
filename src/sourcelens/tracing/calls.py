import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sourcelens.persistence.models import Symbol

Resolution = Literal["resolved", "ambiguous"]

CALL_PATTERN = re.compile(r"(?:(self|this|cls)\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# A definition's own signature (def foo(, class Foo(, function foo() looks
# identical to a call to `foo` under CALL_PATTERN; this excludes it.
DEFINITION_PREFIX = re.compile(r"(?:^|[^\w])(?:def|class|function)\s*$")

# Language keywords and common builtins that would otherwise show up as
# noisy "calls" with no real target (control flow isn't a call; print/len/
# console.log aren't symbols this repository defines).
EXCLUDED_NAMES = {
    "if",
    "elif",
    "for",
    "while",
    "switch",
    "catch",
    "with",
    "return",
    "yield",
    "raise",
    "assert",
    "print",
    "len",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "super",
    "type",
    "isinstance",
    "range",
    "enumerate",
    "zip",
    "map",
    "filter",
    "sorted",
    "console",
    "require",
    "function",
    "async",
    "await",
}


@dataclass(frozen=True)
class CallEdge:
    caller_id: UUID
    callee_id: UUID
    line: int
    resolution: Resolution
    confidence: float
    candidates: list[UUID]


def _snippet(symbol: Symbol, content: str) -> str:
    return content.encode()[symbol.start_byte : symbol.end_byte].decode(errors="ignore")


def detect_calls(symbol_rows: list[Symbol], content_by_file: dict[UUID, str]) -> list[CallEdge]:
    """A static, name-based call graph — not full semantic resolution (no
    type inference), so every edge's `resolution` says how confidently it
    was matched rather than asserting a single truth.

    `self.foo()` / `this.foo()` / `cls.foo()` resolves against sibling
    methods of the same class first (higher confidence, since the qualifier
    narrows the search). A bare `foo()` resolves against a repository-wide
    name index: exactly one symbol named `foo` -> resolved; more than one
    (e.g. two unrelated classes each with a `save` method) -> ambiguous, and
    every candidate is listed rather than guessing one; zero matches (a
    builtin or third-party call) -> no edge at all, since there is nothing
    in this repository to cite as the target.
    """
    callables = [s for s in symbol_rows if s.kind in ("function", "method")]
    by_name: dict[str, list[Symbol]] = {}
    for symbol in symbol_rows:
        if symbol.kind in ("function", "method", "class"):
            by_name.setdefault(symbol.name, []).append(symbol)

    by_class_method: dict[tuple[UUID, str], list[Symbol]] = {}
    for symbol in symbol_rows:
        if symbol.kind == "method" and symbol.parent_id is not None:
            by_class_method.setdefault((symbol.parent_id, symbol.name), []).append(symbol)

    edges: list[CallEdge] = []
    for caller in callables:
        content = content_by_file.get(caller.file_id, "")
        snippet = _snippet(caller, content)
        for line_offset, line in enumerate(snippet.splitlines()):
            for match in CALL_PATTERN.finditer(line):
                qualifier, name = match.groups()
                if name in EXCLUDED_NAMES:
                    continue
                if DEFINITION_PREFIX.search(line[: match.start()]):
                    continue
                line_no = caller.start_line + line_offset

                candidates: list[Symbol] = []
                if qualifier in ("self", "this", "cls") and caller.parent_id is not None:
                    candidates = by_class_method.get((caller.parent_id, name), [])
                if not candidates:
                    candidates = by_name.get(name, [])
                if not candidates:
                    continue

                if len(candidates) == 1:
                    target = candidates[0]
                    confidence = 0.8 if qualifier else 0.6
                    edges.append(
                        CallEdge(caller.id, target.id, line_no, "resolved", confidence, [target.id])
                    )
                else:
                    candidate_ids = [c.id for c in candidates]
                    for target in candidates:
                        edges.append(
                            CallEdge(caller.id, target.id, line_no, "ambiguous", 0.3, candidate_ids)
                        )
    return edges
