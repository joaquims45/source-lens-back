from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from sourcelens.agent.graph import AgentState, build_graph
from sourcelens.agent.models import get_chat_model
from sourcelens.agent.tools import build_tools
from sourcelens.api.errors import DomainError
from sourcelens.config import Settings, get_settings
from sourcelens.persistence.models import Analysis, ChatMessage, Conversation


@dataclass(frozen=True)
class Citation:
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class AgentAnswer:
    conversation_id: UUID
    answer: str
    citations: list[Citation]


def _load_history(db: Session, conversation_id: UUID) -> list[AnyMessage]:
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.sequence)
    ).all()
    messages: list[AnyMessage] = []
    for row in rows:
        messages.append(HumanMessage(row.content) if row.role == "user" else AIMessage(row.content))
    return messages


def _get_or_create_conversation(
    db: Session, analysis_id: UUID, conversation_id: UUID | None
) -> Conversation:
    if db.get(Analysis, analysis_id) is None:
        raise DomainError("analysis_not_found", "Analysis not found", 404)
    if conversation_id is None:
        conversation = Conversation(analysis_id=analysis_id)
        db.add(conversation)
        db.flush()
        return conversation
    existing = db.get(Conversation, conversation_id)
    if existing is None or existing.analysis_id != analysis_id:
        raise DomainError("conversation_not_found", "Conversation not found", 404)
    return existing


def _persist_turn(
    db: Session,
    conversation: Conversation,
    next_sequence: int,
    question: str,
    answer_text: str,
    citations: list[Citation],
) -> None:
    db.add(
        ChatMessage(
            conversation_id=conversation.id, sequence=next_sequence, role="user", content=question
        )
    )
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            sequence=next_sequence + 1,
            role="assistant",
            content=answer_text,
            citations=[citation.__dict__ for citation in citations],
        )
    )


def ask(
    db: Session,
    analysis_id: UUID,
    question: str,
    conversation_id: UUID | None = None,
    model: BaseChatModel | None = None,
    settings: Settings | None = None,
) -> AgentAnswer:
    """Answers a question grounded in one analysis's code. Citations in the
    returned answer are exactly the deduplicated tool evidence the graph
    collected — never text the model wrote itself — so an answer can never
    cite a file or line the agent didn't actually retrieve.
    """
    conversation = _get_or_create_conversation(db, analysis_id, conversation_id)
    settings = settings or get_settings()
    history = _load_history(db, conversation.id)
    next_sequence = len(history)

    tools = build_tools(db, analysis_id, settings)
    graph = build_graph(model or get_chat_model(settings), tools, settings.agent_max_iterations)
    initial_state: AgentState = {
        "messages": [*history, HumanMessage(question)],
        "evidence": [],
        "iterations": 0,
    }
    result = graph.invoke(initial_state)

    answer_text = str(result["messages"][-1].content)
    citations = [Citation(**item) for item in result["evidence"]]
    _persist_turn(db, conversation, next_sequence, question, answer_text, citations)
    return AgentAnswer(conversation.id, answer_text, citations)


def stream_ask(
    db: Session,
    analysis_id: UUID,
    question: str,
    conversation_id: UUID | None = None,
    model: BaseChatModel | None = None,
    settings: Settings | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Same contract as `ask`, but yields `(event, payload)` pairs as the
    graph progresses — validated, structured blocks (a tool being called,
    evidence harvested, the final answer), never raw token fragments — so a
    client can show live progress before persisting the turn once finished.
    """
    conversation = _get_or_create_conversation(db, analysis_id, conversation_id)
    settings = settings or get_settings()
    history = _load_history(db, conversation.id)
    next_sequence = len(history)

    tools = build_tools(db, analysis_id, settings)
    graph = build_graph(model or get_chat_model(settings), tools, settings.agent_max_iterations)
    initial_state: AgentState = {
        "messages": [*history, HumanMessage(question)],
        "evidence": [],
        "iterations": 0,
    }

    answer_text = ""
    citations: list[Citation] = []
    for update in graph.stream(initial_state, stream_mode="updates"):
        if "agent" in update:
            message = update["agent"]["messages"][-1]
            if message.tool_calls:
                for call in message.tool_calls:
                    yield "tool_call", {"name": call["name"], "args": call["args"]}
            else:
                answer_text = str(message.content)
                yield "answer", {"text": answer_text}
        elif "harvest" in update:
            citations = [Citation(**item) for item in update["harvest"]["evidence"]]
            yield "evidence", {"citations": [c.__dict__ for c in citations]}

    _persist_turn(db, conversation, next_sequence, question, answer_text, citations)
    yield "done", {"conversation_id": str(conversation.id)}
