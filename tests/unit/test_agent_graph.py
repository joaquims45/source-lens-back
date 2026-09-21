from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool

from sourcelens.agent.graph import build_graph


class ScriptedChatModel(BaseChatModel):
    """A deterministic stand-in for a real chat model: it plays back a fixed
    script of AIMessages rather than calling any API, so the graph's wiring
    (tool loop, evidence harvesting, iteration cap) can be tested without
    network access or an API key — the same offline-first pattern as
    `HashingEmbeddings`.
    """

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

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


@tool(response_format="content_and_artifact")
def fake_search(query: str) -> tuple[str, list[dict[str, object]]]:
    """Search for something."""
    return f"found: {query}", [{"path": "a.py", "start_line": 1, "end_line": 3}]


def call_message(query: str, call_id: str = "1") -> AIMessage:
    call = {"name": "fake_search", "args": {"query": query}, "id": call_id, "type": "tool_call"}
    return AIMessage(content="", tool_calls=[call])


def test_agent_answers_directly_when_no_tool_call_is_needed():
    model = ScriptedChatModel(script=[AIMessage(content="It just works.")])
    graph = build_graph(model, [fake_search], max_iterations=6)

    result = graph.invoke(
        {"messages": [HumanMessage("hello")], "evidence": [], "iterations": 0}
    )

    assert result["messages"][-1].content == "It just works."
    assert result["evidence"] == []


def test_agent_grounds_its_answer_in_tool_evidence():
    final = AIMessage(content="Orders are created in a.py.")
    model = ScriptedChatModel(script=[call_message("orders"), final])
    graph = build_graph(model, [fake_search], max_iterations=6)

    result = graph.invoke(
        {"messages": [HumanMessage("how are orders created?")], "evidence": [], "iterations": 0}
    )

    assert result["messages"][-1].content == "Orders are created in a.py."
    assert result["evidence"] == [{"path": "a.py", "start_line": 1, "end_line": 3}]


def test_agent_deduplicates_repeated_evidence_across_tool_calls():
    call = call_message("orders")
    model = ScriptedChatModel(script=[call, call, AIMessage(content="Done.")])
    graph = build_graph(model, [fake_search], max_iterations=6)

    result = graph.invoke(
        {"messages": [HumanMessage("q")], "evidence": [], "iterations": 0}
    )

    assert result["evidence"] == [{"path": "a.py", "start_line": 1, "end_line": 3}]


def test_agent_stops_calling_tools_once_the_iteration_budget_is_spent():
    call = call_message("x")
    # A real, unbound model invocation cannot return tool_calls (it wasn't
    # given any tool schema), so the forced final turn scripts a plain answer.
    model = ScriptedChatModel(script=[call, AIMessage(content="Best effort answer.")])
    graph = build_graph(model, [fake_search], max_iterations=2)

    result = graph.invoke({"messages": [HumanMessage("q")], "evidence": [], "iterations": 0})

    assert result["iterations"] == 2
    assert result["messages"][-1].content == "Best effort answer."
    assert len(model.calls) == 2
