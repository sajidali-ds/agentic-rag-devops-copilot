import json

import logfire

from app.agents.state import AgentState
from app.tools.registry import compact, run_tool


def tools_node(state: AgentState):
    """Execute the tool the agent chose and append the observation."""
    action = state.get("next_action") or {}
    name, args = action.get("name", ""), action.get("args", {}) or {}
    with logfire.span("Tool Call", tool=name):
        result = run_tool(name, args)
    logfire.info(f"Tool {name} -> {'error' if 'error' in result else 'ok'}")
    return {
        "scratchpad": [{"type": "tool", "name": name, "args": json.dumps(args, sort_keys=True), "observation": compact(result)}],
        "tools_used": [name],
        "steps": state.get("steps", 0) + 1,
        "plan": list(state.get("plan", [])) + [f"Tool: {name}" + (" (error)" if "error" in result else "")],
    }
