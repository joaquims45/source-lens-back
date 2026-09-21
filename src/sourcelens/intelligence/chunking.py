from bisect import bisect_right
from dataclasses import dataclass
from hashlib import sha256

from sourcelens.intelligence.models import ParsedFile

CHUNKER_VERSION = "ast-byte-budget-v1"


@dataclass(frozen=True)
class CodeChunk:
    content: str
    context_header: str
    symbol_index: int | None
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    kind: str
    split_reason: str | None
    token_count: int
    content_hash: str


def chunks(
    content: str, path: str, language: str, parsed: ParsedFile, max_tokens: int = 1200
) -> list[CodeChunk]:
    """UTF-8 byte count is a conservative budget for byte-level tokenizers.

    No model or tokenizer download is needed in M1. M2 must retokenize with its
    embedding profile. token_count is explicitly labeled byte_upper_bound in API.
    Every non-whitespace source byte is covered once, with exact source offsets.
    """
    if parsed.tree is None:
        return []
    source = content.encode("utf-8")
    boundaries = {0, len(source)}
    syntax_boundaries = {0, len(source)}
    for declaration in parsed.symbols:
        boundaries.update([declaration.start_byte, declaration.end_byte])
    stack = [parsed.tree.root_node]
    while stack:
        node = stack.pop()
        # Prefer declarations, statements and blocks over arbitrary identifier cuts.
        if node.type.endswith(("statement", "definition", "declaration", "block")):
            syntax_boundaries.update([node.start_byte, node.end_byte])
        stack.extend(node.named_children)
    syntax = sorted(syntax_boundaries)
    lines = [index for index, byte in enumerate(source) if byte == 10]
    points = sorted(boundaries)
    result: list[CodeChunk] = []
    for begin, end in zip(points, points[1:], strict=False):
        owner = next(
            (
                index
                for index in range(len(parsed.symbols) - 1, -1, -1)
                if parsed.symbols[index].start_byte <= begin
                and parsed.symbols[index].end_byte >= end
            ),
            None,
        )
        symbol = parsed.symbols[owner] if owner is not None else None
        label = symbol.qualified_name if symbol else "<module>"
        header = f"file: {path}\nlanguage: {language}\nsymbol: {label}"
        # Headers are contextual metadata and are never assigned source line numbers.
        header = header.encode("utf-8")[: max_tokens // 3].decode("utf-8", errors="ignore")
        allowance = max_tokens - len(header.encode("utf-8"))
        if allowance < 4:
            raise ValueError("Chunk budget must leave at least four source bytes")
        cursor = begin
        while cursor < end:
            limit = min(end, cursor + allowance)
            reason = None
            if limit < end:
                cut = syntax[bisect_right(syntax, limit) - 1]
                if cut > cursor:
                    limit, reason = cut, "syntax_boundary"
                else:
                    reason = "oversized_syntax_node"
                    while limit > cursor and source[limit] & 0xC0 == 0x80:
                        limit -= 1
            piece = source[cursor:limit].decode("utf-8")
            if piece.strip():
                result.append(
                    CodeChunk(
                        piece,
                        header,
                        owner,
                        bisect_right(lines, cursor - 1) + 1,
                        bisect_right(lines, limit - 2) + 1,
                        cursor,
                        limit,
                        symbol.kind if symbol else "module",
                        reason,
                        len(header.encode("utf-8")) + len(piece.encode("utf-8")),
                        sha256(piece.encode("utf-8")).hexdigest(),
                    )
                )
            cursor = limit
    return result
