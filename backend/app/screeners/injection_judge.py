"""Injection detection screener (§3.3, §5.7).

Heuristic detection of prompt injection attempts:
  - injection_heuristic: known injection patterns ("ignore previous", "you are", etc.)
  - spoofed_sender: sender details inconsistent
  - encoded_payload: base64/hex encoded content that could hide instructions

All return QUARANTINE (never escalate to BLOCK — that's content_safety's job).
Gemini judge is stubbed behind the Screener interface (M5 heuristic only;
M6 will add the Gemini judge call via the provider router).
"""

from __future__ import annotations

import base64
import re

from app.domain.enums import Decision
from app.screeners.protocol import ScreenContext, Verdict

# Known prompt injection patterns (case-insensitive)
_INJECTION_PATTERNS = [
    re.compile(r"ignore (all )?previous (instructions?|prompts?)", re.I),
    re.compile(r"you are (now )?(an? )?\w+", re.I),
    re.compile(r"forget (everything|all|your instructions)", re.I),
    re.compile(r"system prompt", re.I),
    re.compile(r"do not (follow|obey) (your |the )?instructions", re.I),
    re.compile(r"new (instructions?|role|persona)", re.I),
    re.compile(r"act as (if )?(you are|a|an)", re.I),
    re.compile(r"reveal (your|the) (system|hidden|secret) (prompt|instructions?)", re.I),
    re.compile(r"\<\/?system\>", re.I),
    re.compile(r"\<\/?instruction", re.I),
]

# Base64 pattern (at least 20 chars of base64 to avoid false positives)
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")
# Hex pattern (at least 40 hex chars)
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{40,}\b")


class InjectionScreener:
    """Heuristic injection detection — no LLM judge in M5."""

    name = "injection"

    async def scan(self, ctx: ScreenContext) -> Verdict:
        text = ctx.text
        metadata = ctx.metadata
        reason_codes: list[str] = []
        evidence: list[str] = []

        # injection_heuristic: check for known injection patterns
        for pattern in _INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                reason_codes.append("injection_heuristic")
                snippet = text[max(0, match.start() - 10):match.end() + 10]
                evidence.append(f"Pattern '{pattern.pattern[:30]}' matched: ...{snippet}...")
                break  # one heuristic hit is enough

        # spoofed_sender: check sender email vs reply-to
        sender = metadata.get("sender_email", "")
        reply_to = metadata.get("reply_to", "")
        if sender and reply_to and "@" in sender and "@" in reply_to:
            sender_domain = sender.split("@")[1].lower()
            reply_domain = reply_to.split("@")[1].lower()
            # Check if they share at least the TLD
            if sender_domain.split(".")[-1] != reply_domain.split(".")[-1]:
                reason_codes.append("spoofed_sender")
                evidence.append(
                    f"Sender TLD mismatch: {sender_domain} vs {reply_domain}"
                )

        # encoded_payload: check for suspicious encoded content
        b64_matches: list[str] = _BASE64_RE.findall(text)
        if b64_matches:
            for b64_match in b64_matches:
                try:
                    decoded = base64.b64decode(b64_match).decode(
                        "utf-8", errors="strict"
                    )
                    # Check if decoded content contains instruction-like words
                    if any(
                        word in decoded.lower()
                        for word in ("ignore", "system", "instruction", "prompt")
                    ):
                        reason_codes.append("encoded_payload")
                        evidence.append(
                            f"Base64 content decodes to text containing "
                            f"instruction-like words: '{decoded[:50]}...'"
                        )
                        break
                except (UnicodeDecodeError, ValueError):
                    pass  # Not valid UTF-8, skip

        hex_matches = _HEX_RE.findall(text)
        if hex_matches and "encoded_payload" not in reason_codes:
            reason_codes.append("encoded_payload")
            evidence.append(f"Long hex string detected: {hex_matches[0][:40]}...")

        if not reason_codes:
            return Verdict(
                stage="injection",
                decision=Decision.ALLOW,
                reason_codes=[],
                evidence=[],
                severity=0,
            )

        return Verdict(
            stage="injection",
            decision=Decision.QUARANTINE,
            reason_codes=reason_codes,
            evidence=evidence,
            severity=60,
        )
