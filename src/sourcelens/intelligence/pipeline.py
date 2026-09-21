from hashlib import sha256
from uuid import UUID, uuid4

from sourcelens.ingestion.discovery import DiscoveredFile
from sourcelens.ingestion.languages import SUPPORTED, detect
from sourcelens.intelligence.chunking import chunks
from sourcelens.intelligence.models import ParsedFile
from sourcelens.intelligence.parser import parse
from sourcelens.persistence.models import Chunk, Import, SourceFile, Symbol


def build_file_artifacts(
    analysis_id: UUID, discovered: DiscoveredFile
) -> tuple[SourceFile, list[Symbol], list[Import], list[Chunk]]:
    """Pure transformation from a discovered file to persistence rows.

    Symbol ids are generated up front so parent/chunk links can be resolved
    without a database round trip, keeping this function DB-free and testable.
    """
    language = detect(discovered.path, discovered.content)
    parsed = (
        parse(discovered.content, language) if language in SUPPORTED else ParsedFile("unsupported")
    )
    file_id = uuid4()
    diagnostics: dict[str, object] = {}
    if parsed.errors:
        diagnostics["errors"] = parsed.errors
    if discovered.redactions:
        diagnostics["redactions"] = discovered.redactions
    file_row = SourceFile(
        id=file_id,
        analysis_id=analysis_id,
        path=discovered.path,
        language=language,
        size_bytes=discovered.size,
        content_hash=sha256(discovered.content.encode()).hexdigest(),
        content=discovered.content,
        parse_status=parsed.status,
        diagnostics=diagnostics,
    )
    symbol_ids = [uuid4() for _ in parsed.symbols]
    symbol_rows = [
        Symbol(
            id=symbol_ids[index],
            analysis_id=analysis_id,
            file_id=file_id,
            parent_id=symbol_ids[symbol.parent_index] if symbol.parent_index is not None else None,
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            kind=symbol.kind,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            start_byte=symbol.start_byte,
            end_byte=symbol.end_byte,
            signature=symbol.signature,
            details={"exported": symbol.exported},
        )
        for index, symbol in enumerate(parsed.symbols)
    ]
    import_rows = [
        Import(
            analysis_id=analysis_id,
            file_id=file_id,
            module=imported.module,
            content=imported.content,
            start_line=imported.start_line,
        )
        for imported in parsed.imports
    ]
    chunk_rows = [
        Chunk(
            analysis_id=analysis_id,
            file_id=file_id,
            symbol_id=symbol_ids[chunk.symbol_index] if chunk.symbol_index is not None else None,
            ordinal=ordinal,
            kind=chunk.kind,
            content=chunk.content,
            context_header=chunk.context_header,
            content_hash=chunk.content_hash,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            token_count=chunk.token_count,
            details={"split_reason": chunk.split_reason} if chunk.split_reason else {},
        )
        for ordinal, chunk in enumerate(
            chunks(discovered.content, discovered.path, language, parsed)
        )
    ]
    return file_row, symbol_rows, import_rows, chunk_rows
