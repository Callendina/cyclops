"""Smoke tests — placeholder package builds and imports."""

import re

import cyclops_ui


def test_imports() -> None:
    assert cyclops_ui is not None


def test_version_is_semver() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", cyclops_ui.__version__), cyclops_ui.__version__
