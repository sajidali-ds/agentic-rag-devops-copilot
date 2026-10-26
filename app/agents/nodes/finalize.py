from app.agents.state import AgentState
from app.tools.command_safety import review_commands, safety_notice

UNVERIFIED_NOTE = (
    "\n\n> ℹ️ Some statements could not be fully verified against the retrieved documents. "
    "Treat them as suggestions and confirm before acting."
)


def finalize_node(state: AgentState):
    """Attach output-side safety (command review) and the verification caveat, then commit the answer."""
    answer = state.get("draft_answer", "")
    reviews = review_commands(answer) if state.get("mode") != "conversational" else []

    if state.get("grounded") is False:
        answer += UNVERIFIED_NOTE
    answer += safety_notice(reviews)

    return {
        "final_answer": answer,
        "command_risks": reviews,
        "messages": [{"role": "assistant", "content": answer}],
    }
