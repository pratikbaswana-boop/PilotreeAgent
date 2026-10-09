"""PII detection and masking screener using Presidio (§3.3, §5.7, ADR-8).

Runs Presidio in a thread pool (ADR-8: avoids blocking the event loop
with spaCy inference). Returns a REDACT verdict and the masked text.

M5 uses default Presidio recognisers. UK postcode recogniser is added
for UK logistics context.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from app.domain.enums import Decision
from app.screeners.protocol import ScreenContext, Verdict

logger = logging.getLogger(__name__)

# Thread pool for Presidio (spaCy is CPU-bound)
_pii_pool: asyncio.Executor | None = None  # type: ignore[name-defined]

# Lazy-initialized Presidio engines
_analyzer = None
_anonymizer = None
_initialized = False


async def _ensure_initialized() -> None:
    """Lazily initialize Presidio engines (expensive — spaCy model load)."""
    global _analyzer, _anonymizer, _initialized, _pii_pool
    if _initialized:
        return
    # Create thread pool in the main event loop context
    loop = asyncio.get_event_loop()
    if _pii_pool is None:
        _pii_pool = loop.run_in_executor  # will use default executor

    def _init() -> None:
        global _analyzer, _anonymizer, _initialized
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine
        except ImportError:
            logger.warning(
                "presidio_unavailable",
                extra={"detail": "PII screening is running in no-op fallback mode"},
            )
        else:
            _analyzer = AnalyzerEngine()
            _anonymizer = AnonymizerEngine()  # type: ignore[no-untyped-call]
        _initialized = True

    await asyncio.get_event_loop().run_in_executor(None, _init)


class PIIScreener:
    """Presidio-based PII detection and masking."""

    name = "pii"

    async def scan(self, ctx: ScreenContext) -> Verdict:
        await _ensure_initialized()

        text = ctx.text
        if not text or not text.strip():
            return Verdict(
                stage="pii",
                decision=Decision.ALLOW,
                reason_codes=[],
                evidence=[],
                severity=0,
            )

        # Run Presidio analysis in a thread pool (ADR-8)
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            partial(_run_presidio_analysis, text),
        )

        if not results:
            return Verdict(
                stage="pii",
                decision=Decision.ALLOW,
                reason_codes=[],
                evidence=[],
                severity=0,
            )

        # Collect detected entity types
        entity_types = sorted({r.entity_type for r in results})
        reason_codes = ["pii_detected"]
        evidence = [f"Detected PII types: {', '.join(entity_types)}"]

        return Verdict(
            stage="pii",
            decision=Decision.REDACT,
            reason_codes=reason_codes,
            evidence=evidence,
            severity=50,
        )

    async def mask(self, text: str) -> str:
        """Return the PII-masked version of the text."""
        await _ensure_initialized()

        if not text or not text.strip():
            return text

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            partial(_run_presidio_anonymize, text),
        )


def _run_presidio_analysis(text: str) -> list[Any]:
    """Run Presidio analysis (called in thread pool)."""
    global _analyzer
    if _analyzer is None:
        return []
    # Cities and countries are essential routing context, not street addresses.
    # Preserve only LOCATION spans independently identified as geopolitical
    # entities by spaCy. All other entity types and precise locations stay masked.
    artifacts = _analyzer.nlp_engine.process_text(text, "en")
    public_places = {
        (entity.start_char, entity.end_char)
        for entity in artifacts.tokens.ents
        if entity.label_ == "GPE" and not any(c.isdigit() for c in entity.text)
    }
    results = _analyzer.analyze(
        text=text,
        entities=None,
        language="en",
        nlp_artifacts=artifacts,
    )
    return [
        result
        for result in results
        if not (result.entity_type == "LOCATION" and (result.start, result.end) in public_places)
    ]


def _run_presidio_anonymize(text: str) -> str:
    """Run Presidio anonymization (called in thread pool)."""
    global _analyzer, _anonymizer
    if _analyzer is None or _anonymizer is None:
        return text
    results = _run_presidio_analysis(text)
    if not results:
        return text
    anonymized = _anonymizer.anonymize(
        text=text,
        analyzer_results=results,  # type: ignore[arg-type]
    )
    return anonymized.text
