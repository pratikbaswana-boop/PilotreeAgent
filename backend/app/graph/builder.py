"""Graph builder (§3.3).

Builds the enquiry_triage LangGraph:
  START -> ingest_validate
  ingest_validate --(error)--> await_review
  ingest_validate --(ok)--> all_screens (4 screens run concurrently)
  all_screens --> merge_verdicts
  merge_verdicts --(BLOCK/QUARANTINE)--> await_review
  merge_verdicts --(ALLOW/WARNING/REDACT)--> llm_call
  llm_call --> validate_output
  validate_output --> await_review
  await_review --> finalize
  finalize --> END

Routing:
  - ingest -> screens only if no error; else -> await_review
  - merge -> llm_call if decision is ALLOW, ALLOW_WITH_WARNING or REDACT
  - merge -> await_review if decision is QUARANTINE or BLOCK
  - All analyses require review (validate_output -> await_review always)
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.domain.enums import Decision
from app.graph.nodes.await_review import await_review
from app.graph.nodes.finalize import finalize
from app.graph.nodes.ingest_validate import ingest_validate
from app.graph.nodes.llm_call import llm_call
from app.graph.nodes.merge_verdicts import merge_verdicts
from app.graph.nodes.screens.content_safety import screen_content_safety
from app.graph.nodes.screens.deterministic import screen_deterministic
from app.graph.nodes.screens.injection import screen_injection
from app.graph.nodes.screens.pii import screen_pii
from app.graph.nodes.validate_output import validate_output
from app.graph.state import TriageState

# Decisions that skip the LLM call and go straight to review
_SKIP_LLM_DECISIONS = {
    Decision.BLOCK.value,
}


def _after_ingest(state: TriageState) -> str:
    """Route after ingest_validate: screens if ok, await_review if error."""
    if state.get("error"):
        return "await_review"
    return "all_screens"


def _after_merge(state: TriageState) -> str:
    """Route after merge_verdicts: LLM if ALLOW/WARNING/REDACT, review if BLOCK."""
    merged: dict[str, Any] = state.get("merged_verdict") or {}
    decision = merged.get("decision", Decision.ALLOW.value)
    if decision in _SKIP_LLM_DECISIONS:
        return "await_review"
    return "llm_call"


async def _all_screens(state: TriageState) -> dict[str, Any]:
    """Run all 4 screens concurrently and collect their verdicts."""
    results = await asyncio.gather(
        screen_deterministic(state),
        screen_injection(state),
        screen_content_safety(state),
        screen_pii(state),
    )

    merged_screens: dict[str, Any] = {}
    masked_text: str | None = None
    for r in results:
        screen_verdicts = r.get("screen_verdicts", {})
        merged_screens.update(screen_verdicts)
        if "masked_text" in r:
            masked_text = r["masked_text"]

    output: dict[str, Any] = {"screen_verdicts": merged_screens}
    if masked_text is not None:
        output["masked_text"] = masked_text
    return output


def build_enquiry_triage_graph(
    checkpointer: Any = None, llm_node: Any = llm_call, progress: Any = None
) -> Any:
    """Build and compile the enquiry_triage graph.

    Returns a compiled LangGraph runnable. The caller must supply
    a checkpointer (AsyncPostgresSaver) when invoking.
    """
    graph = StateGraph(TriageState)

    def tracked(name: str, node: Any) -> Any:
        async def invoke(state: TriageState) -> dict[str, Any]:
            if progress is not None:
                await progress(name)
            return await node(state)

        return invoke

    graph.add_node("ingest_validate", tracked("ingest_validate", ingest_validate))
    graph.add_node("all_screens", tracked("all_screens", _all_screens))
    graph.add_node("merge_verdicts", tracked("merge_verdicts", merge_verdicts))
    graph.add_node("llm_call", tracked("llm_call", llm_node))
    graph.add_node("validate_output", tracked("validate_output", validate_output))
    graph.add_node("await_review", tracked("await_review", await_review))
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "ingest_validate")
    graph.add_conditional_edges(
        "ingest_validate",
        _after_ingest,
        {"all_screens": "all_screens", "await_review": "await_review"},
    )
    graph.add_edge("all_screens", "merge_verdicts")
    graph.add_conditional_edges(
        "merge_verdicts",
        _after_merge,
        {"llm_call": "llm_call", "await_review": "await_review"},
    )
    graph.add_edge("llm_call", "validate_output")
    graph.add_edge("validate_output", "await_review")
    graph.add_edge("await_review", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)
