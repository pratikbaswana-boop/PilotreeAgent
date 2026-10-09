"""Deterministic heuristics screener (§3.3, §5.7).

Checks that can be done purely with pattern matching — no LLM needed:
  - missing_name: no customer name provided
  - invalid_email: email format validation
  - domain_mismatch: reply-to domain vs sender domain
  - status_conflict: listed status vs message content

All return ALLOW_WITH_WARNING (never escalate beyond that).
"""

from __future__ import annotations

import re

from app.domain.enums import Decision
from app.screeners.protocol import ScreenContext, Verdict

# Email regex — basic RFC 5322 subset
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


class DeterministicScreener:
    """Deterministic heuristic checks — no LLM, no external calls."""

    name = "deterministic"

    async def scan(self, ctx: ScreenContext) -> Verdict:
        reason_codes: list[str] = []
        evidence: list[str] = []

        text = ctx.text
        metadata = ctx.metadata

        # missing_name: no customer name in metadata or text
        customer_name = metadata.get("customer_name", "")
        if not customer_name or not customer_name.strip():
            reason_codes.append("missing_name")
            evidence.append("No customer_name in metadata")

        # invalid_email: check if email is present but malformed
        email = metadata.get("customer_email", "")
        if email and not _EMAIL_RE.match(email):
            reason_codes.append("invalid_email")
            evidence.append(f"Email '{email[:30]}...' failed format check")

        # domain_mismatch: reply-to domain vs sender domain
        reply_to = metadata.get("reply_to", "")
        sender = metadata.get("sender_email", "")
        if reply_to and sender and "@" in reply_to and "@" in sender:
            reply_domain = reply_to.split("@")[1].lower()
            sender_domain = sender.split("@")[1].lower()
            if reply_domain != sender_domain:
                reason_codes.append("domain_mismatch")
                evidence.append(
                    f"Reply-to domain '{reply_domain}' != sender domain " f"'{sender_domain}'"
                )

        # status_conflict: listed status says delivered but message says
        # "not delivered" or vice versa
        listed_status = metadata.get("status", "").lower()
        if listed_status:
            text_lower = text.lower()
            if "delivered" in listed_status and "not delivered" in text_lower:
                reason_codes.append("status_conflict")
                evidence.append("Status says 'delivered' but message says 'not delivered'")
            elif "pending" in listed_status and "delivered" in text_lower:
                reason_codes.append("status_conflict")
                evidence.append("Status says 'pending' but message says 'delivered'")

        if not reason_codes:
            return Verdict(
                stage="deterministic",
                decision=Decision.ALLOW,
                reason_codes=[],
                evidence=[],
                severity=0,
            )

        return Verdict(
            stage="deterministic",
            decision=Decision.ALLOW_WITH_WARNING,
            reason_codes=reason_codes,
            evidence=evidence,
            severity=20,
        )
