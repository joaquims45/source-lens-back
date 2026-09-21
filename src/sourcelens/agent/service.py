from dataclasses import dataclass
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
    if db.get(Analysis, analysis_id) is None:
        raise DomainError("analysis_not_found", "Analysis not found", 404)

    conversation: Conversation
    if conversation_id is None:
        conversation = Conversation(analysis_id=analysis_id)
        db.add(conversation)
        db.flush()
    else:
        existing = db.get(Conversation, conversation_id)
        if existing is None or existing.analysis_id != analysis_id:
            raise DomainError("conversation_not_found", "Conversation not found", 404)
        conversation = existing

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

    return AgentAnswer(conversation.id, answer_text, citations)
