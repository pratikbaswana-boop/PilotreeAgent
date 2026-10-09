"""merge_verdicts node (§3.3 — screen_merge).

Merges parallel screener verdicts using the escalation-only reducer:
ALLOW < ALLOW_WITH_WARNING < REDACT < QUARANTINE < BLOCK.
Safety decisions escalate only and are preserved through graph reducers.
"""

from __future__ import annotations

from typing import Any

from app.domain.enums import Decision
from app.graph.state import TriageState

# Escalation order — higher index = more severe.
_DECISION_ORDER = {
    Decision.ALLOW: 0,
    Decision.ALLOW_WITH_WARNING: 1,
    Decision.REDACT: 2,
    Decision.QUARANTINE: 3,
    Decision.BLOCK: 4,
}


def escalate(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the most severe verdict from a list of verdict dicts."""
    if not decisions:
        return {
            "stage": "merged",
            "decision": Decision.ALLOW.value,
            "reason_codes": [],
            "evidence": [],
            "severity": 0,
        }

    worst = decisions[0]
    worst_rank = _DECISION_ORDER.get(Decision(worst.get("decision", "ALLOW")), 0)
    all_reason_codes: list[str] = list(worst.get("reason_codes", []))
    all_evidence: list[str] = list(worst.get("evidence", []))
    max_severity = worst.get("severity", 0)

    for v in decisions[1:]:
        rank = _DECISION_ORDER.get(Decision(v.get("decision", "ALLOW")), 0)
        if rank > worst_rank:
            worst = v
            worst_rank = rank
        all_reason_codes.extend(v.get("reason_codes", []))
        all_evidence.extend(v.get("evidence", []))
        max_severity = max(max_severity, v.get("severity", 0))

    return {
        "stage": "merged",
        "decision": worst.get("decision", Decision.ALLOW.value),
        "reason_codes": list(set(all_reason_codes)),
        "evidence": list(set(all_evidence)),
        "severity": max_severity,
    }


async def merge_verdicts(state: TriageState) -> dict[str, Any]:
    """Merge parallel screen verdicts — escalation-only."""
    raw_verdicts = state.get("screen_verdicts", {})
    all_verdicts: list[dict[str, Any]] = []
    for v in raw_verdicts.values():
        if v is not None and isinstance(v, dict):
            all_verdicts.append(v)
    merged = escalate(all_verdicts)
    return {"merged_verdict": merged, "final_verdict": merged}
