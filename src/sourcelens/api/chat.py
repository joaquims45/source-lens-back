import json
from collections.abc import Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from sourcelens.agent import service
from sourcelens.api.deps import get_db
from sourcelens.api.errors import DomainError
from sourcelens.api.schemas import (
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    CitationResponse,
    ConversationDetail,
    ConversationSummary,
)
from sourcelens.persistence.database import session as open_session
from sourcelens.persistence.models import Analysis, ChatMessage, Conversation

router = APIRouter(prefix="/api/v1")


def _to_response(answer: service.AgentAnswer) -> ChatResponse:
    return ChatResponse(
        conversation_id=answer.conversation_id,
        answer=answer.answer,
        citations=[
            CitationResponse(path=c.path, start_line=c.start_line, end_line=c.end_line)
            for c in answer.citations
        ],
    )


@router.post("/analyses/{analysis_id}/chat")
def chat(analysis_id: UUID, body: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    answer = service.ask(db, analysis_id, body.question, conversation_id=body.conversation_id)
    return _to_response(answer)


@router.get("/analyses/{analysis_id}/chat/stream")
def chat_stream(
    analysis_id: UUID,
    q: str = Query(min_length=1),
    conversation_id: UUID | None = Query(None),
) -> StreamingResponse:
    """Streams validated, structured blocks — a tool being called, evidence
    harvested, the final answer, then a `done` event — rather than raw token
    fragments, per the Milestone 0 streaming design. Preconditions are
    checked before the stream opens so a 404 is a normal JSON error, not a
    broken SSE stream.
    """
    with open_session() as db:
        if db.get(Analysis, analysis_id) is None:
            raise DomainError("analysis_not_found", "Analysis not found", 404)
        if conversation_id is not None:
            conversation = db.get(Conversation, conversation_id)
            if conversation is None or conversation.analysis_id != analysis_id:
                raise DomainError("conversation_not_found", "Conversation not found", 404)

    def generate() -> Iterator[str]:
        with open_session() as db, db.begin():
            events = service.stream_ask(db, analysis_id, q, conversation_id=conversation_id)
            for event, payload in events:
                yield f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/analyses/{analysis_id}/conversations")
def list_conversations(
    analysis_id: UUID, db: Session = Depends(get_db)
) -> list[ConversationSummary]:
    if db.get(Analysis, analysis_id) is None:
        raise DomainError("analysis_not_found", "Analysis not found", 404)
    conversations = db.scalars(
        select(Conversation)
        .where(Conversation.analysis_id == analysis_id)
        .order_by(Conversation.created_at.desc())
        .limit(50)
    ).all()
    first_messages = db.scalars(
        select(ChatMessage).where(
            ChatMessage.conversation_id.in_([c.id for c in conversations]),
            ChatMessage.sequence == 0,
        )
    ).all()
    preview_by_conversation = {m.conversation_id: m.content for m in first_messages}
    return [
        ConversationSummary(
            id=c.id, created_at=c.created_at, preview=preview_by_conversation.get(c.id)
        )
        for c in conversations
    ]


@router.get("/analyses/{analysis_id}/conversations/{conversation_id}")
def get_conversation(
    analysis_id: UUID, conversation_id: UUID, db: Session = Depends(get_db)
) -> ConversationDetail:
    if db.get(Analysis, analysis_id) is None:
        raise DomainError("analysis_not_found", "Analysis not found", 404)
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.analysis_id != analysis_id:
        raise DomainError("conversation_not_found", "Conversation not found", 404)
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.sequence)
    ).all()
    return ConversationDetail(
        id=conversation.id,
        created_at=conversation.created_at,
        messages=[
            ChatMessageResponse(
                id=m.id,
                role=m.role,
                content=m.content,
                citations=[CitationResponse(**c) for c in m.citations],
                created_at=m.created_at,
            )
            for m in messages
        ],
    )
