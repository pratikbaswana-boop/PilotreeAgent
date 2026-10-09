"""Domain enums — mirrors the Postgres enums in §4.1 DDL."""

import enum


class UserRole(str, enum.Enum):
    """CREATE TYPE user_role AS ENUM ('viewer','reviewer','admin')."""

    viewer = "viewer"
    reviewer = "reviewer"
    admin = "admin"

    @property
    def rank(self) -> int:
        """Higher = more permissions. Used by RBAC dependency."""
        return _RANK[self]


_RANK = {UserRole.viewer: 0, UserRole.reviewer: 1, UserRole.admin: 2}


class Priority(str, enum.Enum):
    """CREATE TYPE priority AS ENUM ('low','medium','high','critical')."""

    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Category(str, enum.Enum):
    """CREATE TYPE category AS ENUM (...)."""

    delivery_issue = "delivery_issue"
    failed_delivery = "failed_delivery"
    damage_claim = "damage_claim"
    billing_query = "billing_query"
    booking_or_quote = "booking_or_quote"
    sales_lead = "sales_lead"
    reporting_request = "reporting_request"
    suspicious = "suspicious"
    other = "other"


class Decision(str, enum.Enum):
    """CREATE TYPE decision AS ENUM ('ALLOW','ALLOW_WITH_WARNING','REDACT','QUARANTINE','BLOCK')."""

    ALLOW = "ALLOW"
    ALLOW_WITH_WARNING = "ALLOW_WITH_WARNING"
    REDACT = "REDACT"
    QUARANTINE = "QUARANTINE"
    BLOCK = "BLOCK"

    @property
    def severity(self) -> int:
        """Severity: ALLOW(0) < ALLOW_WITH_WARNING(1) < REDACT(2) < QUARANTINE(3) < BLOCK(4)."""
        return _SEVERITY[self]


_SEVERITY = {
    Decision.ALLOW: 0,
    Decision.ALLOW_WITH_WARNING: 1,
    Decision.REDACT: 2,
    Decision.QUARANTINE: 3,
    Decision.BLOCK: 4,
}


class ActionStatus(str, enum.Enum):
    """CREATE TYPE action_status AS ENUM ('pending','sending','sent',
    'failed','unknown','dead_letter')."""

    pending = "pending"
    sending = "sending"
    sent = "sent"
    failed = "failed"
    unknown = "unknown"
    dead_letter = "dead_letter"


class JobStatus(str, enum.Enum):
    """CREATE TYPE job_status AS ENUM ('queued','running','succeeded','dead_letter')."""

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    dead_letter = "dead_letter"
