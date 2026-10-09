"""full DDL: all remaining tables

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-02 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enum types ─────────────────────────────────────────────────
    # priority and category are JSONB-only (used inside AnalysisOut), not column types;
    # they must be created explicitly. decision/action_status/job_status are column types
    # and are created automatically by op.create_table via sa.Enum(...).
    op.execute("CREATE TYPE priority AS ENUM ('low','medium','high','critical')")
    op.execute(
        "CREATE TYPE category AS ENUM ('delivery_issue','failed_delivery','damage_claim',"
        "'billing_query','booking_or_quote','sales_lead','reporting_request','suspicious','other')"
    )

    # ── enquiries ──────────────────────────────────────────────────
    op.create_table(
        "enquiries",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("company", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("warning_missing_name", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("warning_invalid_email", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("warning_domain_mismatch", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("warning_possible_duplicate", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("duplicate_of", sa.Text, sa.ForeignKey("enquiries.id"), nullable=True),
        sa.Column("claimed_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("char_length(message) <= 5000", name="enquiries_message_len"),
    )
    op.create_index("idx_enquiries_company", "enquiries", ["company"])
    op.create_index("idx_enquiries_status", "enquiries", ["status"])
    op.create_index("idx_enquiries_received_at", "enquiries", [sa.text("received_at DESC")])
    op.execute(
        "CREATE INDEX idx_enquiries_search ON enquiries"
        " USING GIN (to_tsvector('english', name || ' ' || company || ' ' || message))"
    )

    # ── analyses ──────────────────────────────────────────────────
    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("enquiry_id", sa.Text, sa.ForeignKey("enquiries.id"), nullable=False),
        sa.Column("analysis_attempt", sa.Integer, nullable=False),
        sa.Column("input_hash", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("schema_version", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("result", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("llm_raw_result", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("usage", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("node_versions", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("job_id", sa.BigInteger, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_inflight_analysis ON analyses (enquiry_id, input_hash, prompt_version)"
        " WHERE status IN ('pending','in_progress')"
    )
    op.create_index("idx_analyses_enquiry", "analyses", ["enquiry_id", sa.text("analysis_attempt DESC")])

    # ── safety_verdicts ────────────────────────────────────────────
    op.create_table(
        "safety_verdicts",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("analysis_id", sa.Uuid, sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("decision", sa.Enum("ALLOW", "ALLOW_WITH_WARNING", "REDACT", "QUARANTINE", "BLOCK", name="decision"),
                  nullable=False),
        sa.Column("reason_codes", sa.dialects.postgresql.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evidence", sa.dialects.postgresql.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("severity", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_verdicts_analysis", "safety_verdicts", ["analysis_id"])

    # ── reviews ───────────────────────────────────────────────────
    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("analysis_id", sa.Uuid, sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("reviewer_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("typed_reason", sa.Text, nullable=True),
        sa.Column("diff", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_reviews_analysis", "reviews", ["analysis_id"])

    # ── actions ───────────────────────────────────────────────────
    op.create_table(
        "actions",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("enquiry_id", sa.Text, sa.ForeignKey("enquiries.id"), nullable=False),
        sa.Column("analysis_id", sa.Uuid, sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("analysis_version", sa.Integer, nullable=False),
        sa.Column("destination", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("resend_of", sa.Uuid, sa.ForeignKey("actions.id"), nullable=True),
        sa.Column("resend_seq", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("resend_reason", sa.Text, nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "sending", "sent", "failed", "unknown", "dead_letter", name="action_status"),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("requested_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("external_id", sa.Text, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sending_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("uq_action_idempotency", "actions", ["idempotency_key"], unique=True)
    op.create_index(
        "idx_actions_enquiry_destination", "actions",
        ["enquiry_id", "destination", sa.text("resend_seq DESC")],
    )

    # ── action_attempts ───────────────────────────────────────────
    op.create_table(
        "action_attempts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("action_id", sa.Uuid, sa.ForeignKey("actions.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("via_reconcile", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("http_status", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_attempts_action", "action_attempts", ["action_id"])

    # ── tool_configs ─────────────────────────────────────────────
    op.create_table(
        "tool_configs",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("config", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("credential_ref", sa.Text, nullable=True),
        sa.Column("encrypted_credential", sa.LargeBinary, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ── audit_log ────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_type", sa.Text, nullable=False, server_default=sa.text("'user'")),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Text, nullable=False),
        sa.Column("metadata", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_audit_entity", "audit_log", ["entity_type", "entity_id"])

    # ── idempotency_keys ──────────────────────────────────────────
    op.create_table(
        "idempotency_keys",
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("response_snapshot", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now() + interval '24 hours'")),
    )

    # ── jobs ─────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "running", "succeeded", "dead_letter", name="job_status"),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("locked_by", sa.Text, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default=sa.text("5")),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("dead_letter_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute(
        "CREATE INDEX idx_jobs_poll ON jobs (status, priority DESC, run_at) WHERE status = 'queued'"
    )

    # ── rate_limit_buckets ───────────────────────────────────────
    op.create_table(
        "rate_limit_buckets",
        sa.Column("bucket_key", sa.Text, primary_key=True),
        sa.Column("tokens", sa.Numeric, nullable=False),
        sa.Column("capacity", sa.Numeric, nullable=False),
        sa.Column("refill_per_sec", sa.Numeric, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ── breaker_state ────────────────────────────────────────────
    op.create_table(
        "breaker_state",
        sa.Column("breaker_key", sa.Text, primary_key=True),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'closed'")),
        sa.Column("failure_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("success_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("half_open_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ── events ───────────────────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_events_topic_id", "events", ["topic", "id"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("breaker_state")
    op.drop_table("rate_limit_buckets")
    op.execute("DROP INDEX IF EXISTS idx_jobs_poll")
    op.drop_table("jobs")
    op.drop_table("idempotency_keys")
    op.drop_index("idx_audit_entity", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("tool_configs")
    op.drop_index("idx_attempts_action", table_name="action_attempts")
    op.drop_table("action_attempts")
    op.drop_index("idx_actions_enquiry_destination", table_name="actions")
    op.drop_index("uq_action_idempotency", table_name="actions")
    op.drop_table("actions")
    op.drop_index("idx_reviews_analysis", table_name="reviews")
    op.drop_table("reviews")
    op.drop_index("idx_verdicts_analysis", table_name="safety_verdicts")
    op.drop_table("safety_verdicts")
    op.drop_index("idx_analyses_enquiry", table_name="analyses")
    op.execute("DROP INDEX IF EXISTS uq_inflight_analysis")
    op.drop_table("analyses")
    op.execute("DROP INDEX IF EXISTS idx_enquiries_search")
    op.drop_index("idx_enquiries_received_at", table_name="enquiries")
    op.drop_index("idx_enquiries_status", table_name="enquiries")
    op.drop_index("idx_enquiries_company", table_name="enquiries")
    op.drop_table("enquiries")

    op.execute("DROP TYPE IF EXISTS job_status")
    op.execute("DROP TYPE IF EXISTS action_status")
    op.execute("DROP TYPE IF EXISTS decision")
    op.execute("DROP TYPE category")
    op.execute("DROP TYPE priority")
