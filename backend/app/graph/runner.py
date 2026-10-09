"""Graph runner (§3.3, §3.7).

Manages the compiled graph, AsyncPostgresSaver checkpointer, and provides
methods to start, resume, and query analysis state.

LangGraph thread_id convention: f"{enquiry_id}:{analysis_attempt}" (§3.7).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.graph.builder import build_enquiry_triage_graph
from app.graph.state import TriageState

logger = logging.getLogger(__name__)

# Singleton compiled graph — built once, reused for all analyses.
_compiled_graph: Any | None = None


def get_compiled_graph() -> Any:
    """Return the singleton compiled graph (no checkpointer — caller provides config)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_enquiry_triage_graph()
    return _compiled_graph


def make_thread_id(enquiry_id: str, analysis_attempt: int) -> str:
    """LangGraph thread_id convention: f"{enquiry_id}:{analysis_attempt}" (§3.7)."""
    return f"{enquiry_id}:{analysis_attempt}"


def make_thread_config(thread_id: str) -> dict[str, Any]:
    """Build the RunnableConfig with the thread_id for checkpointing."""
    return {"configurable": {"thread_id": thread_id}}


async def start_analysis(
    saver: AsyncPostgresSaver,
    enquiry_id: str,
    analysis_attempt: int,
    raw_enquiry: dict[str, Any],
) -> dict[str, Any]:
    """Start a new graph invocation. Returns the state after the first interrupt (await_review).

    Raises if the graph completes without interrupting (shouldn't happen
    since await_review always interrupts).
    """
    graph = build_enquiry_triage_graph(checkpointer=saver)
    thread_id = make_thread_id(enquiry_id, analysis_attempt)
    config = make_thread_config(thread_id)

    initial_state: TriageState = {
        "enquiry_id": enquiry_id,
        "analysis_attempt": analysis_attempt,
        "raw": raw_enquiry,
        "node_versions": {},
    }

    result = await graph.ainvoke(
        initial_state,
        config=config,
    )

    return dict(result)


async def resume_analysis(
    saver: AsyncPostgresSaver,
    enquiry_id: str,
    analysis_attempt: int,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Resume a paused graph with a review decision via Command(resume=...)."""
    graph = build_enquiry_triage_graph(checkpointer=saver)
    thread_id = make_thread_id(enquiry_id, analysis_attempt)
    config = make_thread_config(thread_id)

    result = await graph.ainvoke(
        Command(resume=decision),
        config=config,
    )

    return dict(result)


async def get_graph_state(
    saver: AsyncPostgresSaver,
    enquiry_id: str,
    analysis_attempt: int,
) -> TriageState | None:
    """Get the current graph state for an analysis."""
    graph = build_enquiry_triage_graph(checkpointer=saver)
    thread_id = make_thread_id(enquiry_id, analysis_attempt)
    config = make_thread_config(thread_id)

    state = await graph.aget_state(config=config)
    if state is None or state.values is None:
        return None
    return dict(state.values)  # type: ignore[return-value]


async def get_review_prompt(
    saver: AsyncPostgresSaver,
    enquiry_id: str,
    analysis_attempt: int,
) -> dict[str, Any] | None:
    """Extract the review prompt from the interrupted graph state.

    The interrupt value is stored in the checkpoint's tasks list.
    """
    graph = build_enquiry_triage_graph(checkpointer=saver)
    thread_id = make_thread_id(enquiry_id, analysis_attempt)
    config = make_thread_config(thread_id)

    state = await graph.aget_state(config=config)
    if state is None or state.next is None:
        return None

    # The interrupt value is in the task interrupts
    for task in state.tasks:
        if hasattr(task, "interrupts") and task.interrupts:
            for interrupt in task.interrupts:
                if interrupt.value:
                    return dict(interrupt.value)
    return None


def parse_json_state(state_json: str | None) -> dict[str, Any] | None:
    """Parse a JSON state blob from the analyses.result_state column."""
    if not state_json:
        return None
    try:
        parsed: dict[str, Any] = json.loads(state_json)
        return parsed
    except (json.JSONDecodeError, TypeError):
        return None


def serialize_state(state: dict[str, Any]) -> str:
    """Serialize graph state to JSON for the analyses.result_state column."""
    return json.dumps(state, default=str)
