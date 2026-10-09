"""Tests for code-computed warnings (pure + DB checks)."""

from app.domain.warnings import compute_pure_warnings

# ── Pure checks ────────────────────────────────────────────────────


def test_missing_name_empty() -> None:
    missing, _ = compute_pure_warnings("", "user@example.com")
    assert missing is True


def test_missing_name_whitespace() -> None:
    missing, _ = compute_pure_warnings("   ", "user@example.com")
    assert missing is True


def test_missing_name_present() -> None:
    missing, _ = compute_pure_warnings("Alice", "user@example.com")
    assert missing is False


def test_invalid_email_missing_at() -> None:
    _, invalid = compute_pure_warnings("Alice", "notanemail")
    assert invalid is True


def test_invalid_email_empty() -> None:
    _, invalid = compute_pure_warnings("Alice", "")
    assert invalid is True


def test_invalid_email_valid() -> None:
    _, invalid = compute_pure_warnings("Alice", "user@example.com")
    assert invalid is False


def test_invalid_email_complex_valid() -> None:
    _, invalid = compute_pure_warnings("Alice", "user.name+tag@sub.example.co.uk")
    assert invalid is False
