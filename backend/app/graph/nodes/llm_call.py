"""Structured triage through the configured provider router."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.graph.nodes.merge_verdicts import escalate
from app.graph.state import AnalysisOut, TriageState
from app.llm.provider import GenerateRequest, ProviderError, SafetyBlocked

SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[2] / "llm" / "prompts" / "logistics_triage.txt"
).read_text()


async def llm_call(state: TriageState, *, router: Any = None, db: Any = None) -> dict[str, Any]:
    """Dependencies are injected by the worker, never checkpointed in state."""
    try:
        if router is None or db is None:
            raise ProviderError("Provider router is not configured")
        response = await router.generate_structured(
            db,
            GenerateRequest(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    {
                        "name_present": bool(
                            state.get("raw", {})
                            .get("metadata", {})
                            .get("customer_name", "")
                            .strip()
                        ),
                        "email_present": bool(
                            state.get("raw", {})
                            .get("metadata", {})
                            .get("customer_email", "")
                            .strip()
                        ),
                        "company_present": bool(
                            state.get("raw", {}).get("metadata", {}).get("company", "").strip()
                        ),
                        "status": state.get("raw", {}).get("metadata", {}).get("status", ""),
                        "message": state.get("masked_text")
                        or state.get("raw", {}).get("message", ""),
                        "available_tools": state.get("raw", {}).get("available_tools", []),
                    }
                ),
                schema_name="AnalysisOut",
            ),
            AnalysisOut,
        )
        available_names = {
            item.get("name")
            for item in state.get("raw", {}).get("available_tools", [])
            if isinstance(item, dict)
        }
        recommended_names = {
            item.get("tool") for item in response.data.get("recommended_tools", [])
        }
        if not recommended_names.issubset(available_names):
            raise ProviderError("AI recommended a tool outside the trusted available_tools list")
        usage = response.usage.model_dump(mode="json")
        usage["prompt_version"] = state.get("prompt_version", "v1")
        if "injection_heuristic" in (
            (state.get("screen_verdicts", {}).get("injection") or {}).get("reason_codes", [])
        ) and "injection_suspected" not in response.data.get("risk_flags", []):
            response.data["risk_flags"].append("injection_suspected")
        output = {
            "llm_result": response.data,
            "llm_usage": usage,
            "node_versions": {
                **state.get("node_versions", {}),
                "llm_call": {
                    "model": usage["model"],
                    "prompt_version": usage["prompt_version"],
                    "schema_version": state.get("schema_version", "v1"),
                },
            },
        }
        if "injection_suspected" in response.data.get("risk_flags", []):
            output["final_verdict"] = escalate(
                [
                    state.get("final_verdict") or {},
                    {
                        "stage": "injection",
                        "decision": "QUARANTINE",
                        "reason_codes": ["injection_judge"],
                        "evidence": [
                            "The analysis identified an attempt to manipulate the AI workflow."
                        ],
                        "severity": 60,
                    },
                ]
            )
        return output
    except ProviderError as exc:
        blocked = isinstance(exc, SafetyBlocked)
        verdict = escalate(
            [
                state.get("final_verdict") or {},
                {
                    "stage": "llm",
                    "decision": "BLOCK" if blocked else "ALLOW_WITH_WARNING",
                    "reason_codes": ["provider_safety_block" if blocked else "ai_unavailable"],
                    "severity": 100 if blocked else 30,
                },
            ]
        )
        return {
            "llm_result": None,
            "final_verdict": verdict,
            "error": {"node": "llm_call", "message": "AI unavailable; triage manually."},
        }
