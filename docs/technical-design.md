# SourceLens technical design

Approved Milestone 0. SourceLens is a modular FastAPI backend with separate worker
processes and an independent React/TypeScript frontend. No monorepo or shared
filesystem contracts. Frontend development begins in Milestone 4 and uses strict
TypeScript, Vite, Tailwind, TanStack Query, Monaco and React Flow.

## Invariants

- PostgreSQL owns durable state; Redis transports Celery jobs.
- Every analysis pins an immutable Git commit and parser/chunker versions.
- Files, symbols, chunks, citations and graphs belong to an analysis.
- Ingestion never executes repository code, installs dependencies or follows links.
- Partial parsing is visible. Unsupported languages are not represented as parsed.
- Milestone 1 exposes static intelligence without embeddings or an LLM.
- Future capabilities are explicit, not simulated progress stages.

## Architecture decisions

1. A modular backend, not microservices: lower operational burden with separate
   ingestion and agent worker processes where concurrency needs differ.
2. PostgreSQL + pgvector: metadata, lexical and semantic retrieval in one database.
   Start with exact similarity, introduce HNSW only after recall measurements.
3. Immutable snapshots: reproducible citations at the cost of extra storage.
4. Celery + transactional outbox + leases: at-least-once delivery, idempotent stages,
   durable progress and recovery. No in-process background tasks for ingestion.
5. Tree-sitter for Python, JavaScript/JSX and TypeScript/TSX: syntactic intelligence,
   not a claim of complete semantic resolution. Pin grammar versions.
6. Functions/methods and structural blocks define chunks. Oversized units retain
   original ranges and parent context. No fixed-character-only chunking.
7. Hybrid retrieval (M2): lexical + semantic, RRF, measured reranking. Version
   embedding profiles and retain candidate scores. Baseline Voyage code embeddings.
8. One LangGraph agent (M3), bounded read tools and evidence validation; no tool
   can change its analysis scope. Stream validated blocks through durable SSE.
9. Architecture (M5) combines static facts and explicit confidence/evidence.
   Call tracing (M6) distinguishes resolved, ambiguous and unresolved relationships.

## Milestone 1 domain and API

Repository -> Analysis -> SourceFile -> Symbol / Import / CodeChunk.
Analysis -> Job -> JobStage / JobEvent. Outbox delivers jobs after DB commit.
REST prefix `/api/v1`; analysis-scoped files/symbols/chunks; file IDs rather than
filesystem paths. POST repository returns repository, analysis and job IDs.
Job status and SSE events report real counters with unknown totals represented
as null. Publication is atomic and failed snapshots cannot masquerade as complete.

## Security and validation

Public github.com HTTPS URLs only. Bounded shallow clone, no submodules or LFS,
controlled Git configuration, symlink/binary/secret exclusion, line-preserving
redaction and temporary cleanup. Runtime/container resource limits supplement
application limits. Local single-operator deployment; authentication and resource
authorization are required before exposing the service publicly.

Test language fixtures, ranges, chunk coverage, malicious inputs, job redelivery,
worker recovery, migrations, PostgreSQL/Redis integration and SSE replay. Ruff,
mypy strict and pytest run before relevant commits. No SQLite substitution for
PostgreSQL. Add retrieval evaluation in M2 and groundedness evaluation in M3.

## Incremental delivery

M1: foundation; Docker; persistence; durable jobs; GitHub validation; clone;
discovery/sanitization; language detection; Python extraction; JS/TS extraction;
chunking; pipeline and inspection API; recovery/security integration; documentation.
Each change is reviewed, tested and committed locally. No push without permission.
