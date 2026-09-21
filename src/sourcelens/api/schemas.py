from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class SubmitRepositoryRequest(BaseModel):
    url: str
    ref: str | None = None


class SubmitRepositoryResponse(BaseModel):
    repository_id: UUID
    analysis_id: UUID
    job_id: UUID
    status: str


class RepositorySummary(BaseModel):
    id: UUID
    owner: str
    name: str
    url: str


class JobSummary(BaseModel):
    status: str
    attempt: int
    error: str | None


class StageSummary(BaseModel):
    name: str
    status: str
    processed: int
    total: int | None
    unit: str
    details: dict[str, Any]


class AnalysisResponse(BaseModel):
    id: UUID
    repository: RepositorySummary
    requested_ref: str | None
    commit_sha: str | None
    status: str
    pipeline_version: str
    versions: dict[str, Any]
    capabilities: dict[str, Any]
    stats: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None
    job: JobSummary | None
    stages: list[StageSummary]


class FileSummary(BaseModel):
    id: UUID
    path: str
    language: str
    size_bytes: int
    parse_status: str


class SymbolSummary(BaseModel):
    id: UUID
    file_id: UUID
    file_path: str
    parent_id: UUID | None
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    signature: str
    exported: bool


class ImportSummary(BaseModel):
    module: str
    content: str
    start_line: int


class ChatRequest(BaseModel):
    question: str
    conversation_id: UUID | None = None


class CitationResponse(BaseModel):
    path: str
    start_line: int
    end_line: int


class ChatResponse(BaseModel):
    conversation_id: UUID
    answer: str
    citations: list[CitationResponse]


class SearchResultResponse(BaseModel):
    chunk_id: UUID
    file_id: UUID
    path: str
    start_line: int
    end_line: int
    context_header: str
    content: str
    semantic_score: float | None
    lexical_score: float | None
    fused_score: float | None
    rerank_score: float | None


class EvidenceResponse(BaseModel):
    file: str
    line: int
    reason: str
    origin: str


class ArchitectureNodeResponse(BaseModel):
    id: str
    label: str
    type: str
    confidence: float
    source_files: list[str]
    metadata: dict[str, Any]
    evidence: list[EvidenceResponse]


class ArchitectureEdgeResponse(BaseModel):
    source: str
    target: str
    type: str
    confidence: float
    evidence: list[EvidenceResponse]


class ArchitectureGraphResponse(BaseModel):
    name: str
    schema_version: str
    nodes: list[ArchitectureNodeResponse]
    edges: list[ArchitectureEdgeResponse]
    warnings: list[str]


class ArchitectureComponentResponse(BaseModel):
    node: ArchitectureNodeResponse
    incoming: list[ArchitectureEdgeResponse]
    outgoing: list[ArchitectureEdgeResponse]


class FileDetail(BaseModel):
    id: UUID
    path: str
    language: str
    size_bytes: int
    parse_status: str
    diagnostics: dict[str, Any]
    content: str
    symbols: list[SymbolSummary]
    imports: list[ImportSummary]
