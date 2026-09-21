from uuid import UUID

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select
from sqlalchemy.orm import Session

from sourcelens.config import Settings
from sourcelens.persistence.models import Analysis, SourceFile, Symbol
from sourcelens.retrieval.embeddings import get_embedding_provider
from sourcelens.retrieval.rerank import OverlapReranker
from sourcelens.retrieval.service import search

Evidence = dict[str, object]
ToolResult = tuple[str, list[Evidence]]

# Repository content is untrusted input (see the Milestone 0 security model):
# anything a tool returns is fenced as data, never as instructions, so text
# embedded in a comment or string like "ignore previous instructions" is
# inert to the model.
UNTRUSTED_OPEN = '<repository_content untrusted="true">'
UNTRUSTED_CLOSE = "</repository_content>"


def fence(text: str) -> str:
    return f"{UNTRUSTED_OPEN}\n{text}\n{UNTRUSTED_CLOSE}"


def build_tools(db: Session, analysis_id: UUID, settings: Settings) -> list[BaseTool]:
    provider = get_embedding_provider(settings)
    reranker = OverlapReranker()

    @tool(response_format="content_and_artifact")
    def search_code(query: str) -> ToolResult:
        """Search the repository for code relevant to a natural-language
        question or keyword (hybrid semantic + lexical search). Returns
        ranked snippets with file/line citations. Use this first for
        open-ended questions like "how does X work" or "where is Y computed".
        """
        results = search(
            db,
            analysis_id,
            query,
            embedding_provider=provider,
            reranker=reranker,
            strategy="hybrid_rerank",
            k=settings.agent_search_k,
        )
        if not results:
            return "No matching code was found.", []
        evidence = [
            {"path": r.path, "start_line": r.start_line, "end_line": r.end_line}
            for r in results
        ]
        blocks = "\n\n".join(
            f"{r.path}:{r.start_line}-{r.end_line}\n{r.context_header}\n{r.content}"
            for r in results
        )
        return fence(blocks), evidence

    @tool(response_format="content_and_artifact")
    def find_symbol(name: str) -> ToolResult:
        """Find classes, functions, methods or interfaces by (partial,
        case-insensitive) qualified name, e.g. "OrderService" or
        "OrderService.createOrder". Returns each match's file and lines.
        """
        rows = db.execute(
            select(Symbol, SourceFile.path)
            .join(SourceFile, Symbol.file_id == SourceFile.id)
            .where(Symbol.analysis_id == analysis_id, Symbol.qualified_name.ilike(f"%{name}%"))
            .order_by(SourceFile.path, Symbol.start_line)
            .limit(20)
        ).all()
        if not rows:
            return f"No symbol matching {name!r} was found.", []
        evidence = [
            {"path": path, "start_line": symbol.start_line, "end_line": symbol.end_line}
            for symbol, path in rows
        ]
        lines = "\n".join(
            f"{symbol.kind} {symbol.qualified_name} — {path}:{symbol.start_line}-{symbol.end_line}"
            for symbol, path in rows
        )
        return fence(lines), evidence

    @tool(response_format="content_and_artifact")
    def read_file(
        path: str, start_line: int | None = None, end_line: int | None = None
    ) -> ToolResult:
        """Read a source file's content by its repository-relative path,
        optionally restricted to a 1-indexed inclusive line range. Use this
        to see the full context around a symbol or search result.
        """
        file = db.scalar(
            select(SourceFile).where(SourceFile.analysis_id == analysis_id, SourceFile.path == path)
        )
        if file is None:
            return f"No file at path {path!r} was found.", []
        lines = file.content.splitlines()
        first = max(1, start_line or 1)
        last = min(len(lines), end_line or len(lines))
        snippet = "\n".join(lines[first - 1 : last])
        evidence = [{"path": path, "start_line": first, "end_line": last}]
        return fence(f"{path}:{first}-{last}\n{snippet}"), evidence

    @tool(response_format="content_and_artifact")
    def find_references(name: str) -> ToolResult:
        """Find where a symbol name is textually referenced across the
        repository (lexical match, not a resolved call graph — dependency
        tracing lands in a later milestone). Useful for "what uses X".
        """
        rows = db.execute(
            select(SourceFile.path, SourceFile.content)
            .where(SourceFile.analysis_id == analysis_id, SourceFile.content.contains(name))
            .limit(10)
        ).all()
        if not rows:
            return f"No textual references to {name!r} were found.", []
        evidence = []
        blocks = []
        for path, content in rows:
            for line_number, line in enumerate(content.splitlines(), start=1):
                if name in line:
                    evidence.append(
                        {"path": path, "start_line": line_number, "end_line": line_number}
                    )
                    blocks.append(f"{path}:{line_number}: {line.strip()}")
                    break
        return fence("\n".join(blocks)), evidence

    @tool(response_format="content_and_artifact")
    def get_file_tree() -> ToolResult:
        """List every discovered file path in the repository, to see the
        overall layout before searching or reading a specific file.
        """
        paths = list(
            db.scalars(
                select(SourceFile.path)
                .where(SourceFile.analysis_id == analysis_id)
                .order_by(SourceFile.path)
            )
        )
        return fence("\n".join(paths)), []

    @tool(response_format="content_and_artifact")
    def get_repository_info() -> ToolResult:
        """Get repository-level facts: detected languages, file/symbol/chunk
        counts and the pinned commit. Useful for "what is this project"
        style questions.
        """
        analysis = db.get(Analysis, analysis_id)
        assert analysis is not None
        summary = (
            f"commit: {analysis.commit_sha}\n"
            f"languages: {analysis.capabilities.get('languages')}\n"
            f"stats: {analysis.stats}"
        )
        return fence(summary), []

    return [
        search_code,
        find_symbol,
        read_file,
        find_references,
        get_file_tree,
        get_repository_info,
    ]
