"""Tests for :mod:`wrapkit.cli`."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from fire import decorators

from wrapkit import cli


class _Tool:
    """A tiny Fire CLI for the tests."""

    def __init__(self, no_cache: bool = False) -> None:
        """
        Keep the global flag.

        Args:
            no_cache (bool): A boolean global flag.
        """
        self.no_cache = no_cache

    def show(self, codes: object = None) -> str:
        """
        Echo the parsed codes and the flag.

        Args:
            codes (object): Anything.

        Returns:
            str: ``repr`` of the values.
        """
        return repr((codes, self.no_cache))

    @staticmethod
    def fail() -> str:
        """
        Raise an expected error.

        Raises:
            ValueError: Always.
        """
        msg = "bad input"
        raise ValueError(msg)

    @staticmethod
    def _hidden(codes: object) -> object:
        """
        Stay out of the command list.

        Args:
            codes (object): Anything.

        Returns:
            object: ``codes``.
        """
        return codes


def test_resolve_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare names go to outputs/; paths with a folder are kept."""
    monkeypatch.chdir(tmp_path)
    assert cli.resolve_out("a.csv") == str(Path("outputs") / "a.csv")
    assert (tmp_path / "outputs").is_dir()
    assert cli.resolve_out("b.csv", tmp_path / "x" / "y") == str(
        tmp_path / "x" / "y" / "b.csv"
    )
    assert cli.resolve_out("dir/c.csv") == "dir/c.csv"


def test_as_text() -> None:
    """Numbers become text; None stays None."""
    assert cli.as_text(2024) == "2024"
    assert cli.as_text(None) is None


def test_bind_bool_flags() -> None:
    """Bare flags are pinned, both spellings, up to a standalone --."""
    argv = ["--no-cache", "show", "--no_cache", "--", "--no-cache", "--help"]
    assert cli.bind_bool_flags(argv, ["no_cache"]) == [
        "--no_cache=True",
        "show",
        "--no_cache=True",
        "--",
        "--no-cache",
        "--help",
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{gen: 01}", {"gen": "01"}),
        ("{gen: 00}", {"gen": "00"}),
        ("{gen: '01', n: [1, 2]}", {"gen": "01", "n": [1, 2]}),
        ("010.5x", "010.5x"),
        ("7", 7),
    ],
)
def test_literal_keeping_codes(raw: str, expected: object) -> None:
    """Leading-zero words stay text; other literals parse as usual."""
    assert cli.literal_keeping_codes(raw) == expected


def test_set_parse_fns_and_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Registered parsers apply through Fire; run pins bool flags."""
    cli.set_parse_fns(_Tool, {"codes": cli.literal_keeping_codes})
    cli.set_parse_fns(_Tool, {"codes": cli.literal_keeping_codes})  # harmless twice
    assert decorators.GetParseFns(_Tool.show)["named"] == {
        "codes": cli.literal_keeping_codes
    }
    assert decorators.GetParseFns(_Tool._hidden)["named"] == {}
    lg = logging.getLogger("wrapkit_cli_test")
    cli.run(
        _Tool,
        name="tool",
        logger=lg,
        expected=(ValueError,),
        argv=["--no-cache", "show", "--codes", "{g: 01}"],
        bool_flags=["no_cache"],
    )
    assert capsys.readouterr().out.strip() == "({'g': '01'}, True)"
    monkeypatch.setattr(sys, "argv", ["tool", "show"])
    cli.run(_Tool, name="tool", logger=lg, expected=(ValueError,))
    assert capsys.readouterr().out.strip() == "(None, False)"


def test_run_expected_error(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """An expected error prints one line, logs the traceback and exits 1."""
    lg = logging.getLogger("wrapkit_cli_test")
    with (
        caplog.at_level(logging.DEBUG, logger="wrapkit_cli_test"),
        pytest.raises(SystemExit) as info,
    ):
        cli.run(_Tool, name="tool", logger=lg, expected=(ValueError,), argv=["fail"])
    assert info.value.code == 1
    assert capsys.readouterr().err.strip() == "Error: bad input"
    assert "Traceback" in caplog.text
