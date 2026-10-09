"""Content safety screener (§3.3, §5.7).

Heuristic detection of safety-critical content:
  - content_threat_violence: BLOCK
  - content_self_harm: BLOCK
  - content_illegal_request: BLOCK
  - content_harassment: ALLOW_WITH_WARNING
  - content_hate: ALLOW_WITH_WARNING
  - content_sexual: ALLOW_WITH_WARNING

M5 uses heuristics only. M6 will add a Gemini safety judge behind the
Screener interface, but heuristics remain the first line of defense.
"""

from __future__ import annotations

import re

from app.domain.enums import Decision
from app.screeners.protocol import ScreenContext, Verdict

# Threat/violence patterns
_THREAT_PATTERNS = [
    re.compile(
        r"\b(kill|murder|shoot|stab|bomb|attack|assault)\b"
        r".{0,30}\b(you|him|her|them|someone|people)\b",
        re.I,
    ),
    re.compile(r"\b(threat(en|ening)?|going to (hurt|kill|attack))\b", re.I),
    re.compile(r"\bweapon\b.{0,20}\b(gun|knife|bomb)\b", re.I),
]

# Self-harm patterns
_SELF_HARM_PATTERNS = [
    re.compile(r"\b(hurt|kill|end) (myself|my life)\b", re.I),
    re.compile(r"\b(self.?harm|cutting myself|suicide|suicidal)\b", re.I),
    re.compile(r"\b(don'?t want to (live|be here|go on))\b", re.I),
]

# Illegal request patterns
_ILLEGAL_PATTERNS = [
    re.compile(r"\b(illegal|smuggl(e|ing)|contra?band|customs evasion)\b", re.I),
    re.compile(r"\b(bribe|corrupt|fraud(ulent)?)\b", re.I),
    re.compile(
        r"\b(fake|forge(d|ry)|counterfeit)\b"
        r".{0,20}\b(document|invoice|certificate)\b",
        re.I,
    ),
]

# Harassment patterns
_HARASSMENT_PATTERNS = [
    re.compile(r"\b(stupid|idiot|moron|useless|incompetent)\b", re.I),
    re.compile(r"\b(shut up|go away|nobody cares)\b", re.I),
]

# Hate speech patterns
_HATE_PATTERNS = [
    re.compile(r"\b(hate|despise|loathe)\b.{0,20}\b(you|them|those people|that group)\b", re.I),
    re.compile(r"\b(racist|sexist|discriminat(ory|e))\b", re.I),
]

# Sexual content patterns
_SEXUAL_PATTERNS = [
    re.compile(r"\b(nude|naked|sexual|explicit)\b", re.I),
]


class ContentSafetyScreener:
    """Heuristic content safety checks."""

    name = "content_safety"

    @staticmethod
    def _snippet(text: str, match: re.Match[str], label: str) -> str:
        """Build an evidence snippet from a regex match."""
        start = max(0, match.start() - 10)
        end = match.end() + 10
        return f"{label}: ...{text[start:end]}..."

    async def scan(self, ctx: ScreenContext) -> Verdict:
        text = ctx.text
        reason_codes: list[str] = []
        evidence: list[str] = []

        # BLOCK-level checks (checked first — escalation-only means
        # if any BLOCK is found, we return BLOCK immediately)

        for pattern in _THREAT_PATTERNS:
            match = pattern.search(text)
            if match:
                reason_codes.append("content_threat_violence")
                evidence.append(self._snippet(text, match, "Threat"))
                return Verdict(
                    stage="content_safety",
                    decision=Decision.BLOCK,
                    reason_codes=reason_codes,
                    evidence=evidence,
                    severity=100,
                )

        for pattern in _SELF_HARM_PATTERNS:
            match = pattern.search(text)
            if match:
                reason_codes.append("content_self_harm")
                evidence.append(self._snippet(text, match, "Self-harm"))
                return Verdict(
                    stage="content_safety",
                    decision=Decision.BLOCK,
                    reason_codes=reason_codes,
                    evidence=evidence,
                    severity=100,
                )

        for pattern in _ILLEGAL_PATTERNS:
            match = pattern.search(text)
            if match:
                reason_codes.append("content_illegal_request")
                evidence.append(self._snippet(text, match, "Illegal request"))
                return Verdict(
                    stage="content_safety",
                    decision=Decision.BLOCK,
                    reason_codes=reason_codes,
                    evidence=evidence,
                    severity=100,
                )

        # ALLOW_WITH_WARNING level checks

        for pattern in _HARASSMENT_PATTERNS:
            match = pattern.search(text)
            if match:
                reason_codes.append("content_harassment")
                evidence.append(self._snippet(text, match, "Harassment"))
                break

        for pattern in _HATE_PATTERNS:
            match = pattern.search(text)
            if match:
                reason_codes.append("content_hate")
                evidence.append(self._snippet(text, match, "Hate"))
                break

        for pattern in _SEXUAL_PATTERNS:
            match = pattern.search(text)
            if match:
                reason_codes.append("content_sexual")
                evidence.append(self._snippet(text, match, "Sexual content"))
                break

        if not reason_codes:
            return Verdict(
                stage="content_safety",
                decision=Decision.ALLOW,
                reason_codes=[],
                evidence=[],
                severity=0,
            )

        return Verdict(
            stage="content_safety",
            decision=Decision.ALLOW_WITH_WARNING,
            reason_codes=reason_codes,
            evidence=evidence,
            severity=40,
        )
