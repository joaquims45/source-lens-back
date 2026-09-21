import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import delete, select

from sourcelens.agent import service as agent_service
from sourcelens.jobs import tasks
from sourcelens.main import create_app
from sourcelens.persistence.database import session
from sourcelens.persistence.models import (
    Analysis,
    ChatMessage,
    Chunk,
    Conversation,
    Event,
    Exclusion,
    Import,
    Job,
    Outbox,
    Repository,
    SourceFile,
    Stage,
    Symbol,
)


class ScriptedChatModel(BaseChatModel):
    script: list[AIMessage] = []
    calls: list[int] = []

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        index = len(self.calls)
        self.calls.append(index)
        return ChatResult(generations=[ChatGeneration(message=self.script[index])])

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


def call_message(tool_name: str, args: dict[str, Any]) -> AIMessage:
    call = {"name": tool_name, "args": args, "id": "1", "type": "tool_call"}
    return AIMessage(content="", tool_calls=[call])


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def analyzed_repository(monkeypatch):
    if os.environ.get("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with migrated PostgreSQL and Redis")
    with session() as db, db.begin():
        repo = Repository(
            canonical_url=f"https://github.com/test/{uuid4()}", owner="test", name="a"
        )
        db.add(repo)
        db.flush()
        analysis = Analysis(repository_id=repo.id)
        db.add(analysis)
        db.flush()
        job = Job(
            analysis_id=analysis.id, idempotency_key=str(uuid4()), request_hash="h", request_id="r"
        )
        db.add(job)
        db.flush()
        ids = (repo.id, analysis.id, job.id)

    repo_id, analysis_id, job_id = ids
    with TemporaryDirectory() as workdir:
        root = Path(workdir)
        (root / "orders.py").write_text(
            "class OrderService:\n    def create_order(self):\n        return 1\n"
        )

        @contextmanager
        def fake_clone(repo, ref, settings, heartbeat):
            yield root, "a" * 40

        monkeypatch.setattr(tasks, "clone_repository", fake_clone)
        tasks.ingest(str(job_id))

    yield analysis_id

    with session() as db, db.begin():
        conversation_ids = list(
            db.scalars(select(Conversation.id).where(Conversation.analysis_id == analysis_id))
        )
        for conversation_id in conversation_ids:
            db.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conversation_id))
        db.execute(delete(Conversation).where(Conversation.analysis_id == analysis_id))
        db.execute(delete(Chunk).where(Chunk.analysis_id == analysis_id))
        db.execute(delete(Import).where(Import.analysis_id == analysis_id))
        db.execute(delete(Symbol).where(Symbol.analysis_id == analysis_id))
        db.execute(delete(Exclusion).where(Exclusion.analysis_id == analysis_id))
        db.execute(delete(SourceFile).where(SourceFile.analysis_id == analysis_id))
        db.execute(delete(Event).where(Event.job_id == job_id))
        db.execute(delete(Stage).where(Stage.job_id == job_id))
        db.execute(delete(Outbox).where(Outbox.job_id == job_id))
        db.execute(delete(Job).where(Job.id == job_id))
        db.execute(delete(Analysis).where(Analysis.id == analysis_id))
        db.execute(delete(Repository).where(Repository.id == repo_id))


def mock_model(monkeypatch, script: list[AIMessage]) -> None:
    monkeypatch.setattr(
        agent_service, "get_chat_model", lambda settings: ScriptedChatModel(script=script)
    )


@pytest.mark.integration
def test_chat_endpoint_returns_grounded_answer(client, analyzed_repository, monkeypatch):
    analysis_id = analyzed_repository
    mock_model(
        monkeypatch,
        [
            call_message("search_code", {"query": "orders"}),
            AIMessage(content="Orders are created by OrderService."),
        ],
    )

    response = client.post(
        f"/api/v1/analyses/{analysis_id}/chat", json={"question": "How are orders created?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Orders are created by OrderService."
    assert body["citations"] and body["citations"][0]["path"] == "orders.py"


@pytest.mark.integration
def test_chat_endpoint_rejects_unknown_analysis(client, monkeypatch):
    mock_model(monkeypatch, [AIMessage(content="unused")])
    response = client.post(f"/api/v1/analyses/{uuid4()}/chat", json={"question": "hi"})
    assert response.status_code == 404


@pytest.mark.integration
def test_chat_stream_emits_structured_blocks_and_persists(client, analyzed_repository, monkeypatch):
    analysis_id = analyzed_repository
    mock_model(
        monkeypatch,
        [
            call_message("search_code", {"query": "orders"}),
            AIMessage(content="Orders are created by OrderService."),
        ],
    )

    with client.stream(
        "GET",
        f"/api/v1/analyses/{analysis_id}/chat/stream",
        params={"q": "How are orders created?"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    blocks = [line for line in body.split("\n\n") if line.startswith("event:")]
    events = [line.splitlines()[0].removeprefix("event: ") for line in blocks]
    assert events == ["tool_call", "evidence", "answer", "done"]

    done_payload = json.loads(blocks[-1].splitlines()[1].removeprefix("data: "))
    conversation_id = done_payload["conversation_id"]

    with session() as db:
        messages = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.sequence)
        ).all()
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].citations[0]["path"] == "orders.py"


@pytest.mark.integration
def test_chat_stream_rejects_unknown_analysis_before_streaming(client):
    response = client.get(
        f"/api/v1/analyses/{uuid4()}/chat/stream", params={"q": "hi"}
    )
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
