from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime

# Fixed at the schema level because pgvector columns are dimensioned; changing
# embedding providers to a different dimensionality needs its own migration.
EMBEDDING_DIMENSIONS = 256


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, datetime: DateTime(timezone=True)}


class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    canonical_url: Mapped[str] = mapped_column(unique=True)
    owner: Mapped[str]
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=now)


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repositories.id"), index=True)
    requested_ref: Mapped[str | None]
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(default="queued")
    pipeline_version: Mapped[str] = mapped_column(default="1")
    versions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now)
    completed_at: Mapped[datetime | None]


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("analyses.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    request_hash: Mapped[str]
    request_id: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")
    attempt: Mapped[int] = mapped_column(default=0)
    lease_owner: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None]
    heartbeat_at: Mapped[datetime | None]
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None]
    event_sequence: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Stage(Base):
    __tablename__ = "job_stages"
    __table_args__ = (CheckConstraint("processed >= 0 AND (total IS NULL OR total >= 0)"),)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    name: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str]
    processed: Mapped[int] = mapped_column(default=0)
    total: Mapped[int | None]
    unit: Mapped[str]
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Event(Base):
    __tablename__ = "job_events"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str]
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(default=now)


class Outbox(Base):
    __tablename__ = "outbox_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    published_at: Mapped[datetime | None]


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint("analysis_id", "path"),
        UniqueConstraint("analysis_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("analyses.id"), index=True)
    path: Mapped[str]
    language: Mapped[str]
    size_bytes: Mapped[int]
    content_hash: Mapped[str]
    content: Mapped[str]
    parse_status: Mapped[str]
    diagnostics: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Exclusion(Base):
    __tablename__ = "file_exclusions"
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("analyses.id"), primary_key=True)
    path: Mapped[str] = mapped_column(primary_key=True)
    reason: Mapped[str]


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        UniqueConstraint("analysis_id", "id"),
        ForeignKeyConstraint(
            ["analysis_id", "file_id"], ["source_files.analysis_id", "source_files.id"]
        ),
        ForeignKeyConstraint(["analysis_id", "parent_id"], ["symbols.analysis_id", "symbols.id"]),
        CheckConstraint("start_line > 0 AND end_line >= start_line"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    analysis_id: Mapped[UUID] = mapped_column(index=True)
    file_id: Mapped[UUID] = mapped_column(index=True)
    parent_id: Mapped[UUID | None]
    name: Mapped[str] = mapped_column(index=True)
    qualified_name: Mapped[str]
    kind: Mapped[str]
    start_line: Mapped[int]
    end_line: Mapped[int]
    start_byte: Mapped[int]
    end_byte: Mapped[int]
    signature: Mapped[str]
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Import(Base):
    __tablename__ = "imports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "file_id"], ["source_files.analysis_id", "source_files.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    analysis_id: Mapped[UUID] = mapped_column(index=True)
    file_id: Mapped[UUID]
    module: Mapped[str]
    content: Mapped[str]
    start_line: Mapped[int]


class Chunk(Base):
    __tablename__ = "code_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "file_id"], ["source_files.analysis_id", "source_files.id"]
        ),
        ForeignKeyConstraint(["analysis_id", "symbol_id"], ["symbols.analysis_id", "symbols.id"]),
        CheckConstraint("start_line > 0 AND end_line >= start_line"),
        UniqueConstraint("file_id", "ordinal"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    analysis_id: Mapped[UUID] = mapped_column(index=True)
    file_id: Mapped[UUID]
    symbol_id: Mapped[UUID | None]
    ordinal: Mapped[int]
    kind: Mapped[str]
    content: Mapped[str]
    context_header: Mapped[str]
    content_hash: Mapped[str]
    start_line: Mapped[int]
    end_line: Mapped[int]
    token_count: Mapped[int]
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None]
