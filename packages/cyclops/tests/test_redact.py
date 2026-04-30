"""Tests for cyclops.redact — pure-function masking helpers."""

from __future__ import annotations

import cyclops
import pytest
from cyclops.redact import redact_email, redact_pan, redact_token

# ---------------------------------------------------------------------------
# redact_pan
# ---------------------------------------------------------------------------


def test_redact_pan_16_digit() -> None:
    assert redact_pan("4111111111111111") == "411111******1111"


def test_redact_pan_19_digit() -> None:
    assert redact_pan("4111111111111111234") == "411111*********1234"


def test_redact_pan_15_amex() -> None:
    assert redact_pan("378282246310005") == "378282*****0005"


def test_redact_pan_short_falls_back_to_last_4() -> None:
    assert redact_pan("4111") == "***4111"
    assert redact_pan("411111") == "***1111"
    assert redact_pan("4111111111") == "***1111"  # exactly 10 — no middle masked


def test_redact_pan_very_short_fully_masked() -> None:
    assert redact_pan("123") == "***"
    assert redact_pan("") == "***"


def test_redact_pan_never_returns_unmasked_value() -> None:
    inputs = ["4111111111111111", "1234", "abc", "", "ab", "abcd"]
    for raw in inputs:
        out = redact_pan(raw)
        # The full original must never be the entire output — at minimum the
        # mask token appears somewhere.
        assert "*" in out, f"{raw!r} → {out!r} contains no mask"


def test_redact_pan_accepts_non_string() -> None:
    assert redact_pan(4111111111111111) == "411111******1111"


def test_redact_pan_is_stable() -> None:
    """Same input always produces same output — useful for dashboard grouping."""
    pan = "4111111111111111"
    assert redact_pan(pan) == redact_pan(pan)


# ---------------------------------------------------------------------------
# redact_email
# ---------------------------------------------------------------------------


def test_redact_email_typical() -> None:
    assert redact_email("alice@example.com") == "a***@example.com"


def test_redact_email_single_char_local() -> None:
    assert redact_email("a@example.com") == "a***@example.com"


def test_redact_email_empty_local() -> None:
    assert redact_email("@example.com") == "***@example.com"


def test_redact_email_no_at_sign() -> None:
    assert redact_email("not-an-email") == "***"
    assert redact_email("") == "***"


def test_redact_email_preserves_domain_for_grouping() -> None:
    """Dashboards filter by domain (for tenant breakdowns) — must be intact."""
    assert "@example.com" in redact_email("alice@example.com")
    assert "@callendina.com" in redact_email("bob@callendina.com")


def test_redact_email_accepts_non_string() -> None:
    class Stringly:
        def __str__(self) -> str:
            return "carol@example.com"

    assert redact_email(Stringly()) == "c***@example.com"


# ---------------------------------------------------------------------------
# redact_token
# ---------------------------------------------------------------------------


def test_redact_token_typical() -> None:
    assert redact_token("sk_live_abcdef1234") == "***1234"


def test_redact_token_4_chars() -> None:
    assert redact_token("abcd") == "***abcd"


def test_redact_token_short_fully_masked() -> None:
    assert redact_token("abc") == "***"
    assert redact_token("") == "***"


def test_redact_token_is_stable() -> None:
    tok = "sk_live_abcdef1234"
    assert redact_token(tok) == redact_token(tok)


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------


def test_redact_helpers_importable_at_top_level() -> None:
    assert cyclops.redact_pan("4111111111111111") == "411111******1111"
    assert cyclops.redact_email("alice@example.com") == "a***@example.com"
    assert cyclops.redact_token("sk_live_abcdef1234") == "***1234"


# ---------------------------------------------------------------------------
# Composition with the forbidden check
# ---------------------------------------------------------------------------


def test_redact_output_used_with_safe_field_name_passes(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The intended pattern: redact value, then store under a non-forbidden name."""
    from cyclops._emitter import _emit

    _emit(
        "vispay.payment.processed",
        "info",
        {
            "outcome": "success",
            "masked_pan": cyclops.redact_pan("4111111111111111"),
            "masked_email": cyclops.redact_email("alice@example.com"),
        },
    )
    out = capsys.readouterr().out
    assert "411111******1111" in out
    assert "a***@example.com" in out
    # Importantly, the unmasked values are NOT in the output.
    assert "4111111111111111" not in out
    assert "alice@example.com" not in out


def test_redact_under_forbidden_name_still_blocked(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Even masked values can't be stored under a forbidden field name —
    the field name is the trigger, not the value."""
    from cyclops._emitter import _emit
    from cyclops.exceptions import CyclopsForbiddenFieldError

    with pytest.raises(CyclopsForbiddenFieldError):
        _emit(
            "vispay.payment.processed",
            "info",
            {"pan": cyclops.redact_pan("4111111111111111")},  # 'pan' is forbidden
        )
    assert capsys.readouterr().out == ""
