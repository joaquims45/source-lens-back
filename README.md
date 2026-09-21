# source-lens-api

Evidence-first repository intelligence backend for **SourceLens**. This README
covers what exists today: **Milestone 1 — Repository Intelligence**,
**Milestone 2 — Search**, **Milestone 3 — AI Agent**,
**Milestone 5 — Architecture Intelligence**,
**Milestone 6 — Dependency Tracing** and
**Milestone 7 — Production Hardening**. (Milestone 4 is `source-lens-web`.)

## What Milestone 1 does

Given a public GitHub repository URL, the backend clones it, discovers and
sanitizes its files, detects languages, parses supported languages with
Tree-sitter, extracts symbols/imports, and splits files into syntax-aware
chunks — all inspectable through a REST API without any LLM involved. See
[`docs/technical-design.md`](docs/technical-design.md) for the full design
and its trade-offs.

```
GitHub URL -> clone -> file discovery -> language detection ->
Tree-sitter parsing -> symbol extraction -> code-aware chunking -> persisted analysis
```

Supported languages: Python, JavaScript, TypeScript, TSX. Other files are
still discovered and stored (for the future file explorer) but marked
`unsupported` rather than silently skipped or misrepresented as parsed.

## What Milestone 2 does

After parsing, ingestion embeds every chunk and stores the vector in
PostgreSQL (`pgvector`). Retrieval combines two independent rankings:

```
                 +-- semantic (pgvector cosine distance) --+
query -----------|                                         |-- Reciprocal Rank Fusion -- rerank -- results
                 +-- lexical (tsvector / ts_rank_cd) -------+
```

- **Semantic search**: cosine distance over `code_chunks.embedding` (exact,
  brute-force — see the trade-off note below).
- **Lexical search**: PostgreSQL full-text search over a generated,
  GIN-indexed `tsvector` column — finds exact identifiers/keywords that
  embeddings can miss.
- **Hybrid**: Reciprocal Rank Fusion (RRF) combines both rankings by
  position, not raw score, since cosine similarity and `ts_rank_cd` aren't on
  comparable scales.
- **Reranking**: a token-overlap baseline (`OverlapReranker`) rescores hybrid
  candidates. It's intentionally simple — no model download or external API
  required — and sits behind the same `Reranker` protocol a cross-encoder or
  hosted rerank API would use later.

Every retrieved chunk keeps its semantic/lexical/fused/rerank scores and its
file/line citation, so retrieval stays inspectable and evaluable — it's never
sent to an LLM (that's Milestone 3).

**Embeddings, pragmatically**: the default `EmbeddingProvider` is a
deterministic, offline hashing scheme (feature hashing over identifier-like
tokens), not a trained code-embedding model — it needs no API key so the
whole pipeline runs locally end to end. `VoyageEmbeddings` (Voyage's code
embedding API, the Milestone 0 baseline choice) is implemented behind the
same interface; set `EMBEDDING_PROVIDER=voyage` and `VOYAGE_API_KEY` to use
it. Swapping providers with a different vector dimension needs a new
migration, since `pgvector` columns are fixed-dimension.

**Evaluating retrieval**: `python -m sourcelens.evaluation.run` measures
Recall@K, MRR and latency for an already-ingested analysis against a
`{question, expected_files}` dataset, across all four strategies:

```bash
uv run python -m sourcelens.evaluation.run \
  --analysis-id <uuid> --dataset eval_dataset.json --k 5
```

```json
[{"question": "How are orders created?", "expected_files": ["src/orders/order.service.ts"]}]
```

## What Milestone 3 does

A single LangGraph agent answers questions about the repository, using
bounded read tools over the M1/M2 data: `search_code`, `find_symbol`,
`read_file`, `find_references`, `trace_dependency` (added in Milestone 6,
once the call graph it needs existed), `get_file_tree` and
`get_repository_info`.

```
agent (LLM + tools) --tool call?--> tools --> harvest evidence --back to agent
        |
        no more tool calls
        v
      answer
```

Grounding is structural, not trust-based: **citations returned to the caller
are exactly the deduplicated tool evidence the graph harvested — never text
the model wrote**. A tool's result (`artifact`) is the only source of
citations, so an answer can't cite a file or line the agent didn't actually
retrieve. Tool output is also fenced (`<repository_content untrusted="true">`)
and the system prompt states explicitly that repository content is data,
never instructions — a comment reading "ignore previous instructions" is
just text to quote, not something the model obeys. A hard iteration cap
forces a tools-off final turn instead of ever looping forever.

Conversations and their citations persist to Postgres
(`conversations`/`chat_messages`), so a `conversation_id` continues a thread.

**Model, pragmatically**: the default chat model is Anthropic's API via
`langchain-anthropic` (`ANTHROPIC_API_KEY` required); without a key, `/chat`
and `/chat/stream` fail clearly (`agent_unconfigured`) while every other
endpoint keeps working. The whole graph — tool loop, evidence dedup,
iteration cap — is independently tested with a scripted fake chat model, the
same offline-first pattern used for embeddings in Milestone 2.

## What Milestone 5 does

The architecture graph (`ArchitectureGraph`) is never built by the LLM: it's
computed on demand from facts Milestones 1–2 already persisted (files,
symbols, imports), plus the repository's own manifests. Every node/edge
carries at least one `Evidence(file, line, reason, origin)` and a confidence
score — the domain model enforces this (see
`sourcelens.architecture.graph.GraphBuilder`), so nothing in the graph is
asserted without a citation.

```
module nodes ---- imports (heuristic token match) ---- module nodes
   |
component nodes (controller/service/repository/worker/authentication)
   |          classified from decorators (@app.get, @celery_app.task — high
   |          confidence) or name suffixes (*Service, *Repository — lower)
   v
infrastructure nodes (database/cache/queue/external_api/infrastructure)
   detected from Dockerfiles, docker-compose service images (strongest
   evidence) and dependency manifests, plus usage patterns inside a
   component's own source (redis.Redis(), session.query(...), httpx.get(...))
```

This computed-on-demand design means detector changes apply retroactively to
any past analysis without re-ingesting it — the graph is a pure view, not
stored state. Test code (`tests/`, `test_*.py`, `*.spec.ts`, …) is excluded
before classification, so a test function that happens to mention "jwt" in
its name doesn't get misclassified as an authentication component.

This is a heuristic baseline, not a resolved call graph — that's Milestone 6.
An import edge cites the actual import statement but only *approximately*
matches it to a module by name; treat low-confidence edges as leads, not
certainties, exactly as their `confidence` field signals.

## What Milestone 6 does

`sourcelens.tracing` answers "who calls this" / "what does this call" for a
selected function or method — a static, name-based call graph, computed on
demand from persisted symbols like the architecture graph, and equally
explicit about not being full semantic resolution:

- `self.foo()` / `this.foo()` / `cls.foo()` resolves against sibling methods
  of the *same class* first (confidence 0.8) — the qualifier narrows the
  search.
- A bare `foo()` resolves against a repository-wide name index: exactly one
  symbol named `foo` -> **resolved** (0.6); more than one (two unrelated
  classes each with a `save` method, say) -> **ambiguous** (0.3, every
  candidate listed — never guessed); zero matches (a builtin or third-party
  call) -> no edge at all, since there's nothing in the repository to cite.
- A definition's own signature (`def foo(`, `class Foo(`) is excluded from
  being read as a call to itself — this exact confusion was a real bug
  caught by the unit tests before it shipped.

`GET /analyses/{id}/symbols/{symbol_id}/trace` returns the selected symbol
plus its callers/callees, each with the citing call site and its
resolved/ambiguous status. The agent also gained a `trace_dependency` tool
now that this data exists (see Milestone 3 above).

## What Milestone 7 does

- **Security**: CORS is an explicit origin allow-list (no wildcard),
  defaulting to the Vite dev server. `API_KEY`, unset by default for the
  local single-operator setup, gates every `/api/*` route behind an
  `X-API-Key` header when set — closing a gap the Milestone 0 design
  explicitly flagged ("authentication ... required before exposing the
  service publicly").
- **Caching**: the architecture graph and call graph are pure, deterministic
  computations over an analysis's persisted facts — expensive to recompute
  (a full regex scan of every symbol's source) for no reason once an
  analysis is `completed` and therefore immutable. Both are cached in Redis
  with a TTL as a memory safety valve, not for invalidation (there's nothing
  to invalidate); a still-running analysis is always computed fresh, since
  caching a graph built from partial data would be wrong.
- **Observability**: `sourcelens.observability` logs retrieval (strategy,
  candidate count, top scores per component, latency) and agent turns
  (model, tools called, iterations, input/output tokens, latency, an
  estimated cost when the model is a recognized one) as structured fields —
  the ones PLAN.MD's observability section calls for. Every field is also a
  natural OpenTelemetry span attribute, so real tracing later means wrapping
  these call sites in spans, not redesigning what gets recorded.

## Architecture

- **API** (`sourcelens.main`): FastAPI app exposing submission and inspection
  endpoints.
- **Dispatcher** (`sourcelens.jobs.dispatcher`): polls an outbox table and
  hands queued/expired jobs to Celery — this is what makes redelivery after a
  worker crash automatic (a job's lease expiring makes it eligible again).
- **Worker** (Celery, `sourcelens.jobs.celery_app` / `sourcelens.jobs.tasks`):
  runs the ingestion pipeline for one job and reports real per-stage progress.
- **PostgreSQL**: durable state — repositories, analyses, jobs, stages,
  events, files, symbols, imports, chunks. Every analysis pins an immutable
  commit SHA and parser/chunker version.
- **Redis**: Celery's broker.

## Running locally

```bash
docker compose up -d           # postgres, redis, api, worker, dispatcher
curl http://127.0.0.1:8000/health/live
```

The `api` container runs pending Alembic migrations on startup. Without
Docker, run each piece against the `postgres`/`redis` services from
`compose.yaml`:

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn sourcelens.main:app --reload
uv run python -m sourcelens.jobs.dispatcher
uv run celery -A sourcelens.jobs.celery_app worker --loglevel=INFO -Q ingestion
```

Configuration is read from environment variables / `.env` (see
`.env.example`); see `sourcelens.config.Settings` for every limit (clone
timeout, max repository size, max files, job lease duration, etc).

## API

All endpoints are under `/api/v1`.

| Method & path | Purpose |
| --- | --- |
| `POST /repositories` | Submit a repository for analysis. Requires an `Idempotency-Key` header; body is `{"url": "...", "ref": "optional-branch-or-tag"}`. Returns `repository_id`, `analysis_id`, `job_id`. |
| `GET /analyses/{id}` | Analysis status, commit SHA, versions, capabilities, stats, and per-stage progress. |
| `GET /analyses/{id}/events` | Server-Sent Events stream of job progress (`stage.*`, `analysis.*`). |
| `GET /analyses/{id}/files` | File list with language and parse status. |
| `GET /analyses/{id}/files/{file_id}` | File content plus its symbols and imports. |
| `GET /analyses/{id}/symbols?q=` | Symbols for the analysis, optionally filtered by qualified name. |
| `GET /analyses/{id}/search?q=&k=&strategy=` | Hybrid code search. `strategy` is `semantic`, `lexical`, `hybrid` or `hybrid_rerank` (default). Returns chunks with file/line citations and per-component scores. |
| `POST /analyses/{id}/chat` | Ask a grounded question. Body `{"question": "...", "conversation_id": null}`. Returns the answer plus citations; omit `conversation_id` to start a new thread, pass it back to continue one. |
| `GET /analyses/{id}/chat/stream?q=&conversation_id=` | Same as above, streamed as SSE events: `tool_call`, `evidence`, `answer`, then `done` (with the conversation id). |
| `GET /analyses/{id}/architecture` | The full architecture graph: nodes and edges, each with its evidence and confidence. |
| `GET /analyses/{id}/architecture/components/{component_id}` | One component's evidence plus its incoming/outgoing dependency edges. |
| `GET /analyses/{id}/symbols/{symbol_id}/trace` | Who calls this symbol and what it calls, each with the citing call site and resolved/ambiguous status. |

Errors are returned as `application/problem+json` with a `request_id` that
matches the `X-Request-ID` response header and the structured logs.

## Security

Repository content is untrusted input: only public `https://github.com/...`
URLs are accepted, clones are shallow and time/size bounded, secrets and
`.env`-like files are excluded or redacted, symlinks are never followed, and
paths are validated against traversal. Repository code is never executed.

The agent extends this to the LLM boundary: everything a tool returns is
repository *data*, never instructions, both structurally (the system prompt)
and textually (an untrusted-content fence around tool output) — see
Milestone 3 above.

At the network boundary (Milestone 7): CORS is an explicit origin
allow-list, and `API_KEY` gates every `/api/*` route via `X-API-Key` when
set (see above) — unset by default, since this is still meant as a local,
single-operator deployment rather than a publicly exposed one.

## Testing

```bash
uv run ruff check .
uv run mypy src
uv run pytest                              # unit tests only
RUN_INTEGRATION=1 uv run pytest            # + integration tests (needs postgres/redis up)
```

Integration tests cover idempotent submission, lease-based job redelivery,
snapshot isolation between analyses, the ingestion task end to end (against a
fake clone step, so they don't depend on network access), lexical/semantic/
hybrid retrieval and evaluation against real Postgres/pgvector, the agent's
tool loop, grounding and conversation persistence against a scripted chat
model (no ANTHROPIC_API_KEY needed to run the suite), and architecture
detection against real committed code (docker-compose parsing, decorator/
name-pattern classification, usage-signal edges).

The architecture and call-graph detectors were also verified against a real
public FastAPI repository (`nsidnev/fastapi-realworld-example-app`) end to
end, which is how the test-code misclassification above was actually caught
before it shipped.

CORS/API-key auth, caching (asserting the detection function only runs once
across repeated requests, not just that a cache function was called) and
structured logging (via structlog's `capture_logs`, asserting real emitted
fields rather than that a log function was called) all have their own
coverage too. 127 tests total. API key auth and CORS were also independently
verified against a live container (`API_KEY` set, wrong/missing/correct
`X-API-Key`, and a real CORS preflight request).
