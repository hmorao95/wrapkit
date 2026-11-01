"""Shared fixtures: no test may touch the real home directory."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """
    Redirect ``Path.home()`` to an empty per-test directory.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Source of the directory.
        monkeypatch (pytest.MonkeyPatch): Undoes the patch after the test.

    Returns:
        Path: The fake home directory.
    """
    fake = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake))
    return fake
