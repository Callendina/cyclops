"""Tests for cyclops._forbidden — name-based enforcement at all depths."""

from __future__ import annotations

import json

import cyclops
import pytest
from cyclops._forbidden import (
    _BASE_FORBIDDEN,
    _MAX_RECURSION_DEPTH,
    check_forbidden,
    get_forbidden_fields,
)
from cyclops.exceptions import CyclopsForbiddenFieldError

# ---------------------------------------------------------------------------
# Base list shape
# ---------------------------------------------------------------------------


def test_base_list_includes_secrets() -> None:
    for name in ["password", "secret", "api_key", "token", "private_key"]:
        assert name in _BASE_FORBIDDEN


def test_base_list_includes_payment_card_data() -> None:
    for name in ["pan", "card_number", "cvv", "track1", "track2", "magstripe"]:
        assert name in _BASE_FORBIDDEN


def test_base_list_includes_auth_factors() -> None:
    for name in ["pin", "pin_block", "otp_secret", "totp_secret"]:
        assert name in _BASE_FORBIDDEN


# ---------------------------------------------------------------------------
# Top-level enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_BASE_FORBIDDEN))
def test_top_level_forbidden_name_raises(name: str) -> None:
    with pytest.raises(CyclopsForbiddenFieldError) as exc_info:
        check_forbidden({name: "value"})
    assert exc_info.value.field_name == name


def test_top_level_safe_name_passes() -> None:
    check_forbidden({"foo": "x", "bar": 42, "user_id": "alice@example.com"})


def test_check_is_case_insensitive() -> None:
    with pytest.raises(CyclopsForbiddenFieldError):
        check_forbidden({"Password": "x"})
    with pytest.raises(CyclopsForbiddenFieldError):
        check_forbidden({"API_KEY": "x"})
    with pytest.raises(CyclopsForbiddenFieldError):
        check_forbidden({"PaN": "x"})


# ---------------------------------------------------------------------------
# Nested enforcement
# ---------------------------------------------------------------------------


def test_nested_dict_forbidden_name_raises() -> None:
    with pytest.raises(CyclopsForbiddenFieldError) as exc_info:
        check_forbidden({"safe": {"deep": {"password": "x"}}})
    assert exc_info.value.field_name == "password"
    assert "safe" in exc_info.value.path
    assert "password" in exc_info.value.path


def test_list_of_dicts_forbidden_name_raises() -> None:
    with pytest.raises(CyclopsForbiddenFieldError):
        check_forbidden({"items": [{"ok": 1}, {"secret": "boom"}]})


def test_tuple_of_dicts_forbidden_name_raises() -> None:
    with pytest.raises(CyclopsForbiddenFieldError):
        check_forbidden({"items": ({"ok": 1}, {"secret": "boom"})})


def test_safe_nested_passes() -> None:
    payload = {
        "outer": {
            "middle": {
                "inner": [
                    {"id": 1, "name": "ok"},
                    {"id": 2, "name": "also-ok"},
                ]
            }
        }
    }
    check_forbidden(payload)


def test_strings_in_lists_are_not_treated_as_keys() -> None:
    # "password" appearing as a *value* in a list isn't forbidden — the
    # check is on names, not values. Pattern-based value scrubbing is out
    # of scope for v1.
    check_forbidden({"notes": ["password mentioned but as a value"]})


# ---------------------------------------------------------------------------
# Depth truncation
# ---------------------------------------------------------------------------


def test_deep_nesting_within_limit_succeeds(configured_env: None) -> None:
    payload: dict[str, object] = {"safe": "value"}
    cur = payload
    for i in range(_MAX_RECURSION_DEPTH):
        cur["next"] = {"id": i}
        cur = cur["next"]  # type: ignore[assignment]
    check_forbidden(payload)


def test_deep_nesting_emits_truncation_warning(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    payload: dict[str, object] = {}
    cur = payload
    # Build _MAX_RECURSION_DEPTH + 5 levels of nesting.
    for _ in range(_MAX_RECURSION_DEPTH + 5):
        cur["next"] = {}
        cur = cur["next"]  # type: ignore[assignment]
    cur["password"] = "deep secret"  # would normally raise

    # Should NOT raise (truncated before reaching depth) and SHOULD emit warning.
    check_forbidden(payload)
    out = capsys.readouterr().out
    warnings = [
        json.loads(line)
        for line in out.splitlines()
        if json.loads(line)["event_type"] == "cyclops.forbidden_check_depth_truncated"
    ]
    assert len(warnings) >= 1
    assert warnings[0]["max_depth"] == _MAX_RECURSION_DEPTH


# ---------------------------------------------------------------------------
# Runtime extension via cyclops.init
# ---------------------------------------------------------------------------


def test_init_adds_extra_forbidden_fields() -> None:
    cyclops.init(extra_forbidden_fields=["customer_full_pan"])
    assert "customer_full_pan" in get_forbidden_fields()
    with pytest.raises(CyclopsForbiddenFieldError):
        check_forbidden({"customer_full_pan": "4111111111111111"})


def test_init_extra_fields_normalised_to_lowercase() -> None:
    cyclops.init(extra_forbidden_fields=["MY_SECRET"])
    with pytest.raises(CyclopsForbiddenFieldError):
        check_forbidden({"my_secret": "x"})
    with pytest.raises(CyclopsForbiddenFieldError):
        check_forbidden({"My_Secret": "x"})


def test_init_does_not_remove_base_entries() -> None:
    """No remove API exists; calling init repeatedly only adds."""
    cyclops.init(extra_forbidden_fields=["foo"])
    cyclops.init(extra_forbidden_fields=["bar"])
    forbidden = get_forbidden_fields()
    assert "foo" in forbidden
    assert "bar" in forbidden
    assert "password" in forbidden  # base preserved


def test_init_with_none_is_noop() -> None:
    initial = get_forbidden_fields()
    cyclops.init()
    assert get_forbidden_fields() == initial


# ---------------------------------------------------------------------------
# Integration with the emitter
# ---------------------------------------------------------------------------


def test_emit_with_forbidden_top_level_raises(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from cyclops._emitter import _emit

    with pytest.raises(CyclopsForbiddenFieldError, match="password"):
        _emit("foo.bar", "info", {"password": "secret"})
    assert capsys.readouterr().out == ""


def test_emit_with_forbidden_nested_raises(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from cyclops._emitter import _emit

    with pytest.raises(CyclopsForbiddenFieldError, match="cvv"):
        _emit(
            "foo.bar",
            "info",
            {"payload": {"card": {"cvv": "123"}}},
        )
    assert capsys.readouterr().out == ""


def test_emit_after_init_extension_blocks_new_field(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from cyclops._emitter import _emit

    cyclops.init(extra_forbidden_fields=["my_app_secret"])
    with pytest.raises(CyclopsForbiddenFieldError):
        _emit("foo.bar", "info", {"my_app_secret": "x"})
    assert capsys.readouterr().out == ""


def test_self_emitted_warnings_use_safe_field_names() -> None:
    """The library's own warning events must not use forbidden field names."""
    safe_warning_fields = {"conflicting_key", "source", "path", "max_depth"}
    forbidden = get_forbidden_fields()
    assert safe_warning_fields.isdisjoint(forbidden)
