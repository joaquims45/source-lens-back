from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from sourcelens.agent.prompts import SYSTEM_PROMPT

Evidence = dict[str, object]


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    evidence: list[Evidence]
    iterations: int


def _merge_evidence(existing: list[Evidence], new: list[Evidence]) -> list[Evidence]:
    seen = {tuple(sorted(item.items())) for item in existing}
    merged = list(existing)
    for item in new:
        key = tuple(sorted(item.items()))
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def build_graph(
    model: BaseChatModel, tools: list[BaseTool], max_iterations: int
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """An explicit agent/tools/harvest loop rather than a prebuilt ReAct
    agent, per the Milestone 0 decision that the flow stay easy to read and
    extend. `harvest` is what turns tool calls into grounded citations: it
    reads each tool's `artifact` (never the model's own words) into
    `evidence`, so a citation can only ever point at something a tool
    actually returned.
    """
    bound_model: Runnable[Any, AIMessage] = model.bind_tools(tools)
    tool_node = ToolNode(tools)

    def agent(state: AgentState) -> dict[str, Any]:
        iterations = state.get("iterations", 0) + 1
        # Once the budget is spent, invoke the model without tools bound so
        # it must answer with what it already has instead of looping forever.
        active_model = bound_model if iterations < max_iterations else model
        response = active_model.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response], "iterations": iterations}

    def harvest(state: AgentState) -> dict[str, Any]:
        new_evidence: list[Evidence] = []
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            if message.artifact:
                new_evidence.extend(message.artifact)
        return {"evidence": _merge_evidence(state.get("evidence", []), new_evidence)}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tool_node)
    graph.add_node("harvest", harvest)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "harvest")
    graph.add_edge("harvest", "agent")
    return graph.compile()
