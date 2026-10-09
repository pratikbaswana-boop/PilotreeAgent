"""Code-computed enquiry warnings (§4.1 DDL, §2.2 data flow).

Four warnings are computed on ingest/edit and stored on the `enquiries` row:
  - `warning_missing_name`      — pure: name is empty/whitespace
  - `warning_invalid_email`     — pure: email doesn't match a basic RFC-ish pattern
  - `warning_domain_mismatch`   — DB: email domain doesn't match any known contact
                                  for this company (A3: seeded from existing enquiries)
  - `warning_possible_duplicate` — DB: same email+company within N hours (Open Q3: exact for v1)

The pure checks are computed without I/O. The DB checks are deterministic (not AI)
and run as part of the ingest/edit transaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Enquiry

# Pragmatic email regex — not RFC-5322 complete, but rejects the common bad cases.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Duplicate window: same email+company within this duration (Open Q3: exact match for v1).
_DUPLICATE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class EnquiryWarnings:
    """Result of computing the four code-computed warnings."""

    warning_missing_name: bool
    warning_invalid_email: bool
    warning_domain_mismatch: bool
    warning_possible_duplicate: bool


def compute_pure_warnings(name: str, email: str) -> tuple[bool, bool]:
    """Pure checks that need no DB access — callable from any context."""
    missing_name = not name or not name.strip()
    invalid_email = not bool(_EMAIL_RE.match(email)) if email else True
    return missing_name, invalid_email


async def compute_db_warnings(
    db: AsyncSession,
    *,
    enquiry_id: str | None,
    email: str,
    company: str,
) -> tuple[bool, bool]:
    """DB-dependent checks: domain mismatch and possible duplicate.

    Both are deterministic (not AI). Runs inside the caller's transaction.
    `enquiry_id` is None for new enquiries (so self is excluded from the
    duplicate query); for edits, the existing id is excluded.
    """
    domain_mismatch = False
    possible_duplicate = False

    # No prior company contacts means unknown, not a mismatch. Compare literal
    # domains (not SQL LIKE patterns) and ignore malformed historical emails.
    if _EMAIL_RE.fullmatch(email.strip()):
        domain = email.strip().rsplit("@", 1)[-1].casefold()
        result = await db.execute(
            select(Enquiry.email).where(
                Enquiry.company == company,
                Enquiry.id != enquiry_id if enquiry_id else sa_text_true(),
            )
        )
        known_domains = {
            address.strip().rsplit("@", 1)[-1].casefold()
            for address in result.scalars()
            if _EMAIL_RE.fullmatch(address.strip())
        }
        domain_mismatch = bool(known_domains) and domain not in known_domains

    # Possible duplicate: same email+company within the duplicate window.
    from datetime import datetime

    cutoff = datetime.now(UTC) - _DUPLICATE_WINDOW
    dup_result = await db.execute(
        select(Enquiry.id)
        .where(
            Enquiry.email == email,
            Enquiry.company == company,
            Enquiry.received_at >= cutoff,
            Enquiry.id != enquiry_id if enquiry_id else sa_text_true(),
        )
        .limit(1)
    )
    possible_duplicate = dup_result.scalar_one_or_none() is not None

    return domain_mismatch, possible_duplicate


def sa_text_true() -> Any:
    """Return a always-true condition for the 'no enquiry_id to exclude' case."""
    from sqlalchemy import text

    return text("1=1")


async def compute_warnings(
    db: AsyncSession,
    *,
    enquiry_id: str | None,
    name: str,
    email: str,
    company: str,
) -> EnquiryWarnings:
    """Compute all four warnings — pure + DB checks in one call."""
    missing_name, invalid_email = compute_pure_warnings(name, email)
    domain_mismatch, possible_duplicate = await compute_db_warnings(
        db,
        enquiry_id=enquiry_id,
        email=email,
        company=company,
    )
    return EnquiryWarnings(
        warning_missing_name=missing_name,
        warning_invalid_email=invalid_email,
        warning_domain_mismatch=domain_mismatch,
        warning_possible_duplicate=possible_duplicate,
    )


def apply_warnings(enquiry: Enquiry, warnings: EnquiryWarnings) -> None:
    """Set the four warning columns on an Enquiry model instance."""
    enquiry.warning_missing_name = warnings.warning_missing_name
    enquiry.warning_invalid_email = warnings.warning_invalid_email
    enquiry.warning_domain_mismatch = warnings.warning_domain_mismatch
    enquiry.warning_possible_duplicate = warnings.warning_possible_duplicate
