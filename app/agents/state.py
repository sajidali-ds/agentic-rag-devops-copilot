import operator
from typing import Annotated, List, TypedDict


class AgentState(TypedDict, total=False):
    # Conversation (history + latest user message). operator.add appends instead of replacing.
    messages: Annotated[List[dict], operator.add]

    # Routing
    mode: str  # "conversational" | "question" | "troubleshoot"
    current_query: str  # first search query, or "CONVERSATIONAL"
    next_action: dict  # {"action": "search"|"tool"|"final", ...} chosen by the agent node

    # Evidence gathered by the loop
    scratchpad: Annotated[List[dict], operator.add]  # observations from searches + tools
    tools_used: Annotated[List[str], operator.add]
    searched: List[str]  # normalised queries already run (loop protection)
    doc_hits: List[dict]  # structured retrieval hits (content, source, scores)
    documents: List[str]  # prompt-ready chunks "[Source: x]\n..."
    citations: List[str]
    steps: int

    # Answer pipeline
    draft_answer: str
    needs_regen: bool
    draft_retry: int
    verify_notes: str
    grounded: bool | None
    command_risks: List[dict]
    final_answer: str

    # Presentation
    plan: List[str]
    status: str
