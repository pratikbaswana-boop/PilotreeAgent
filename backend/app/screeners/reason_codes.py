"""Reason codes (§5.7 — reason code table).

Single source of truth for reason codes, reused by backend screeners
and (generated into) frontend reasonCodes.ts for parity.
"""

from __future__ import annotations

from app.domain.enums import Decision

# (code, stage, decision, banner_copy)
REASON_CODES: dict[str, tuple[str, str, Decision, str]] = {
    "missing_name": (
        "missing_name", "deterministic", Decision.ALLOW_WITH_WARNING,
        "No name was provided for this contact.",
    ),
    "invalid_email": (
        "invalid_email", "deterministic", Decision.ALLOW_WITH_WARNING,
        "The email address format looks invalid.",
    ),
    "domain_mismatch": (
        "domain_mismatch", "deterministic", Decision.ALLOW_WITH_WARNING,
        "This email domain doesn't match any known contact for this company.",
    ),
    "possible_duplicate": (
        "possible_duplicate", "deterministic", Decision.ALLOW_WITH_WARNING,
        "This looks similar to another recent enquiry — check for duplicates.",
    ),
    "status_conflict": (
        "status_conflict", "deterministic", Decision.ALLOW_WITH_WARNING,
        "The listed status doesn't match what the message says — "
        "we've trusted the message.",
    ),
    "pii_detected": (
        "pii_detected", "pii", Decision.REDACT,
        "We've masked personal details ({types}) this step doesn't need. "
        "Reveal requires permission.",
    ),
    "injection_heuristic": (
        "injection_heuristic", "injection", Decision.QUARANTINE,
        "This message contains phrasing that looks like an attempt to "
        "manipulate the AI. The AI result is unverified.",
    ),
    "injection_judge": (
        "injection_judge", "injection", Decision.QUARANTINE,
        "Our AI safety check flagged possible prompt manipulation. "
        "The AI result is unverified.",
    ),
    "spoofed_sender": (
        "spoofed_sender", "injection", Decision.QUARANTINE,
        "The sender details look inconsistent or spoofed. "
        "The AI result is unverified.",
    ),
    "encoded_payload": (
        "encoded_payload", "injection", Decision.QUARANTINE,
        "This message contains encoded content that could hide instructions. "
        "The AI result is unverified.",
    ),
    "content_threat_violence": (
        "content_threat_violence", "content_safety", Decision.BLOCK,
        "This message contains content suggesting a threat of violence. "
        "Escalate to your manager/security immediately — "
        "do not send to integrations.",
    ),
    "content_self_harm": (
        "content_self_harm", "content_safety", Decision.BLOCK,
        "This message mentions self-harm. "
        "Escalate per your safeguarding process — do not send to integrations.",
    ),
    "content_illegal_request": (
        "content_illegal_request", "content_safety", Decision.BLOCK,
        "This message appears to request something illegal. "
        "Escalate for manual handling.",
    ),
    "content_harassment": (
        "content_harassment", "content_safety", Decision.ALLOW_WITH_WARNING,
        "This message contains language that may be harassing or abusive.",
    ),
    "content_hate": (
        "content_hate", "content_safety", Decision.ALLOW_WITH_WARNING,
        "This message contains language that may be hateful or discriminatory.",
    ),
    "content_sexual": (
        "content_sexual", "content_safety", Decision.ALLOW_WITH_WARNING,
        "This message contains sexual content.",
    ),
    "provider_safety_block": (
        "provider_safety_block", "content_safety", Decision.QUARANTINE,
        "The AI provider declined to process this content. Review it manually.",
    ),
    "output_invented_fact": (
        "output_invented_fact", "output_validate", Decision.BLOCK,
        "The AI result was discarded because it referenced details not "
        "present in the message.",
    ),
    "output_leakage": (
        "output_leakage", "output_validate", Decision.BLOCK,
        "The AI result was discarded because it may have leaked "
        "internal instructions.",
    ),
    "cold_chain_floor": (
        "cold_chain_floor", "policy", Decision.ALLOW_WITH_WARNING,
        "Priority was raised because this looks time/temperature-sensitive.",
    ),
}


def get_banner_copy(code: str) -> str:
    """Get the UI banner copy for a reason code."""
    entry = REASON_CODES.get(code)
    if entry is None:
        return ""
    return entry[3]
