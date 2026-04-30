"""Smoke tests — prove the package builds, imports, and exposes its version."""

import re

import cyclops


def test_imports() -> None:
    assert cyclops is not None


def test_version_is_semver() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", cyclops.__version__), cyclops.__version__
