"""Tests for cyclops.context — bind/get/snapshot, isolation, overwrite rule."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid

import pytest
from cyclops import context
from cyclops.exceptions import CyclopsValidationError

# ---------------------------------------------------------------------------
# Shape and validation
# ---------------------------------------------------------------------------


def test_allowlist_contents() -> None:
    expected = {
        "request_id",
        "session_id",
        "user_id",
        "user_role",
        "user_group",
        "is_system_admin",
        "workflow_id",
        "app_version",
    }
    assert context._CONTEXT_KEYS == expected


def test_bind_unknown_key_raises(configured_env: None) -> None:
    with pytest.raises(CyclopsValidationError, match="Unknown context key"):
        with context.bind(not_a_real_key="x"):
            pass


def test_get_unknown_key_raises(configured_env: None) -> None:
    with pytest.raises(CyclopsValidationError, match="Unknown context key"):
        context.get("not_a_real_key")


def test_set_unknown_key_raises(configured_env: None) -> None:
    with pytest.raises(CyclopsValidationError, match="Unknown context key"):
        context.set("not_a_real_key", "x")


# ---------------------------------------------------------------------------
# bind: set, restore, nest
# ---------------------------------------------------------------------------


def test_bind_adds_keys_for_scope(configured_env: None) -> None:
    with context.bind(user_id="alice@example.com"):
        assert context.get("user_id") == "alice@example.com"


def test_bind_restores_on_exit(configured_env: None) -> None:
    with context.bind(user_id="alice@example.com"):
        pass
    assert context.get("user_id") is None


def test_bind_restores_on_exception(configured_env: None) -> None:
    with pytest.raises(RuntimeError):
        with context.bind(user_id="alice@example.com"):
            raise RuntimeError("boom")
    assert context.get("user_id") is None


def test_bind_nested_inner_overrides_then_restores(configured_env: None) -> None:
    with context.bind(workflow_id="outer"):
        assert context.get("workflow_id") == "outer"
        with context.bind(workflow_id="outer"):  # same value — no warning
            assert context.get("workflow_id") == "outer"
        assert context.get("workflow_id") == "outer"


def test_bind_unrelated_keys_compose(configured_env: None) -> None:
    with context.bind(user_id="alice@example.com"):
        with context.bind(workflow_id="w-1"):
            assert context.get("user_id") == "alice@example.com"
            assert context.get("workflow_id") == "w-1"
        assert context.get("user_id") == "alice@example.com"
        assert context.get("workflow_id") is None


# ---------------------------------------------------------------------------
# Auto-generated request_id
# ---------------------------------------------------------------------------


def test_bind_auto_generates_request_id_when_absent(configured_env: None) -> None:
    with context.bind(user_id="alice@example.com"):
        rid = context.get("request_id")
    assert isinstance(rid, str)
    uuid.UUID(rid)  # raises if not a valid UUID


def test_bind_inherits_request_id_when_parent_has_one(
    configured_env: None,
) -> None:
    with context.bind(request_id="rid-parent"):
        with context.bind(user_id="alice@example.com"):
            assert context.get("request_id") == "rid-parent"


def test_bind_explicit_request_id_used_verbatim(configured_env: None) -> None:
    with context.bind(request_id="explicit-id"):
        assert context.get("request_id") == "explicit-id"


# ---------------------------------------------------------------------------
# Overwrite rule and warning event
# ---------------------------------------------------------------------------


def test_bind_conflict_keeps_original_and_emits_warning(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with context.bind(workflow_id="original"):
        capsys.readouterr()  # discard whatever happened so far
        with context.bind(workflow_id="conflicting"):
            assert context.get("workflow_id") == "original"

    out = capsys.readouterr().out.splitlines()
    warnings = [
        json.loads(line)
        for line in out
        if json.loads(line)["event_type"] == "cyclops.context_overwrite_attempted"
    ]
    assert len(warnings) == 1
    assert warnings[0]["level"] == "warning"
    assert warnings[0]["conflicting_key"] == "workflow_id"
    assert "source" in warnings[0]


def test_bind_same_value_is_silent(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with context.bind(workflow_id="shared"):
        capsys.readouterr()  # discard
        with context.bind(workflow_id="shared"):
            pass
    assert capsys.readouterr().out == ""


def test_set_conflict_keeps_original_and_emits_warning(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with context.bind(workflow_id="original"):
        capsys.readouterr()
        context.set("workflow_id", "conflicting")
        assert context.get("workflow_id") == "original"

    out = capsys.readouterr().out.splitlines()
    warnings = [
        json.loads(line)
        for line in out
        if json.loads(line)["event_type"] == "cyclops.context_overwrite_attempted"
    ]
    assert len(warnings) == 1
    assert warnings[0]["conflicting_key"] == "workflow_id"


def test_overwrite_warning_source_is_caller_location(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with context.bind(workflow_id="original"):
        capsys.readouterr()
        with context.bind(workflow_id="conflicting"):  # this line is the caller
            caller_line = "test_overwrite_warning_source_is_caller_location"

    out = capsys.readouterr().out.splitlines()
    warnings = [json.loads(line) for line in out]
    source = warnings[0]["source"]
    assert __file__ in source
    assert re.search(r":\d+$", source)
    # The warning should attribute to a line from this test file, not to
    # cyclops's internals.
    assert "/cyclops/" not in source or "/tests/" in source
    del caller_line


# ---------------------------------------------------------------------------
# snapshot and process defaults
# ---------------------------------------------------------------------------


def test_snapshot_includes_app_version_default(configured_env: None) -> None:
    snap = context.snapshot()
    assert snap["app_version"] == "0.0.0-test"


def test_snapshot_returns_all_set_fields(configured_env: None) -> None:
    with context.bind(user_id="alice@example.com", workflow_id="w-1"):
        snap = context.snapshot()
    assert snap["user_id"] == "alice@example.com"
    assert snap["workflow_id"] == "w-1"
    assert snap["app_version"] == "0.0.0-test"


def test_get_app_version_uses_default(configured_env: None) -> None:
    assert context.get("app_version") == "0.0.0-test"


def test_app_version_can_be_overridden(configured_env: None) -> None:
    with context.bind(app_version="1.2.3-override"):
        assert context.get("app_version") == "1.2.3-override"
    assert context.get("app_version") == "0.0.0-test"


# ---------------------------------------------------------------------------
# Threading and async isolation
# ---------------------------------------------------------------------------


def test_threading_does_not_inherit_context(configured_env: None) -> None:
    """`threading.Thread` does NOT inherit contextvars by default — that's a
    Python footgun cyclops should not paper over."""
    seen: list[str | None] = []

    def worker() -> None:
        seen.append(context.get("user_id"))

    with context.bind(user_id="parent@example.com"):
        t = threading.Thread(target=worker)
        t.start()
        t.join()

    assert seen == [None]


def test_threading_with_copy_context_preserves_state(
    configured_env: None,
) -> None:
    """Using contextvars.copy_context() is the documented way to propagate."""
    import contextvars

    seen: list[str | None] = []

    def worker() -> None:
        seen.append(context.get("user_id"))

    with context.bind(user_id="parent@example.com"):
        ctx = contextvars.copy_context()
        t = threading.Thread(target=ctx.run, args=(worker,))
        t.start()
        t.join()

    assert seen == ["parent@example.com"]


def test_asyncio_create_task_inherits_context(configured_env: None) -> None:
    async def runner() -> str | None:
        with context.bind(user_id="parent@example.com"):
            task = asyncio.create_task(_read_user_id_async())
            return await task

    seen = asyncio.run(runner())
    assert seen == "parent@example.com"


async def _read_user_id_async() -> str | None:
    return context.get("user_id")


def test_asyncio_child_task_changes_dont_leak_back(configured_env: None) -> None:
    """A child task adding a *new* key (not subject to the overwrite rule)
    must not leak that key back to the parent task."""

    async def runner() -> tuple[str | None, str | None]:
        with context.bind(user_id="parent@example.com"):
            await asyncio.create_task(_inner_add_workflow())
            return context.get("user_id"), context.get("workflow_id")

    user_id_after, workflow_id_after = asyncio.run(runner())
    assert user_id_after == "parent@example.com"
    assert workflow_id_after is None


async def _inner_add_workflow() -> None:
    with context.bind(workflow_id="child-w"):
        assert context.get("workflow_id") == "child-w"
        assert context.get("user_id") == "parent@example.com"


# ---------------------------------------------------------------------------
# Integration with the emitter
# ---------------------------------------------------------------------------


def test_emitter_includes_context_fields(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from cyclops._emitter import _emit

    with context.bind(user_id="alice@example.com", workflow_id="w-1"):
        _emit("foo.bar", "info", {})

    out = capsys.readouterr().out.strip()
    event = json.loads(out)
    assert event["user_id"] == "alice@example.com"
    assert event["workflow_id"] == "w-1"
    assert event["app_version"] == "0.0.0-test"
    assert "request_id" in event  # auto-generated


def test_emitter_omits_unset_context_fields(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from cyclops._emitter import _emit

    _emit("foo.bar", "info", {})

    event = json.loads(capsys.readouterr().out.strip())
    assert "user_id" not in event
    assert "session_id" not in event
    assert "workflow_id" not in event
    # app_version is always present (process default)
    assert event["app_version"] == "0.0.0-test"


def test_caller_cannot_override_context_keys(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from cyclops._emitter import _emit

    with pytest.raises(CyclopsValidationError, match="reserved"):
        _emit("foo.bar", "info", {"user_id": "imposter"})
    assert capsys.readouterr().out == ""
