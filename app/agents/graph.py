from langgraph.graph import END, StateGraph

from app.agents.nodes.agent import agent_node
from app.agents.nodes.finalize import finalize_node
from app.agents.nodes.planner import planner_node
from app.agents.nodes.responder import generate_node
from app.agents.nodes.search import search_node
from app.agents.nodes.tools import tools_node
from app.agents.nodes.verify import verify_node
from app.agents.state import AgentState

# Worst case path ≈ 2×AGENT_MAX_STEPS + 9 nodes; give LangGraph headroom (default limit is 25).
RECURSION_LIMIT = 60


def route_planner(state: AgentState) -> str:
    return "respond" if state.get("mode") == "conversational" else "search"


def route_agent(state: AgentState) -> str:
    action = (state.get("next_action") or {}).get("action", "final")
    return {"search": "search", "tool": "tools"}.get(action, "respond")


def route_verify(state: AgentState) -> str:
    return "respond" if state.get("needs_regen") else "finalize"


workflow = StateGraph(AgentState)
workflow.add_node("planner", planner_node)
workflow.add_node("search", search_node)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tools_node)
workflow.add_node("respond", generate_node)
workflow.add_node("verify", verify_node)
workflow.add_node("finalize", finalize_node)

workflow.set_entry_point("planner")
workflow.add_conditional_edges("planner", route_planner, {"search": "search", "respond": "respond"})
workflow.add_edge("search", "agent")
workflow.add_conditional_edges("agent", route_agent, {"search": "search", "tools": "tools", "respond": "respond"})
workflow.add_edge("tools", "agent")
workflow.add_edge("respond", "verify")
workflow.add_conditional_edges("verify", route_verify, {"respond": "respond", "finalize": "finalize"})
workflow.add_edge("finalize", END)

# Stateless: conversation history lives in the shared store (see app/cache/conversation.py),
# so any API replica can serve any turn. No in-process MemorySaver.
rag_agent = workflow.compile()
