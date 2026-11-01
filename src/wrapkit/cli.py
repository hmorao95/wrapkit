"""
Helpers for the wrappers' python-fire command lines.

Fire turns a class's methods into subcommands with no hand-written parsing,
but a few of its habits need correcting in every wrapper:

* a bare boolean flag takes the next word as its value, so ``--no-cache
  browse`` swallows the subcommand (:func:`bind_bool_flags`);
* arguments that look like numbers arrive as ints, so ``--start 2024`` is
  ``2024`` and the code ``01`` cannot be written bare (:func:`as_text`,
  :func:`literal_keeping_codes`, :func:`set_parse_fns`).

:func:`run` wraps the Fire call itself: expected errors print a short
``Error: ...`` line and exit 1, with the traceback sent to the log.
:func:`resolve_out` routes bare output file names into ``outputs/``.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Iterable, Mapping

__all__ = [
    "as_text",
    "bind_bool_flags",
    "literal_keeping_codes",
    "resolve_out",
    "run",
    "set_parse_fns",
]


def resolve_out(path: str, outputs: str | Path = "outputs") -> str:
    """
    Route a bare filename into the outputs folder.

    A path with no directory part (``data.csv``) is written under ``outputs``
    (created if needed); a path that already has a directory, relative or
    absolute, is returned unchanged.

    Args:
        path (str): The user-supplied destination.
        outputs (str | Path): The folder for bare names.

    Returns:
        str: The path to write to.
    """
    candidate = Path(path)
    if candidate.parent == Path():
        folder = Path(outputs)
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder / candidate.name)
    return path


def as_text(value: object) -> str | None:
    """
    Pass a CLI value through as text.

    Fire parses arguments that look like numbers, so ``--start 2024`` arrives
    as the int ``2024``. Turning it back into a string keeps a ``str | None``
    contract and gives the same query (and cache entry) as the library call.

    Args:
        value (object): The value Fire passed, or ``None``.

    Returns:
        str | None: ``None`` unchanged, anything else as ``str(value)``.
    """
    return None if value is None else str(value)


def bind_bool_flags(argv: list[str], flags: Iterable[str]) -> list[str]:
    """
    Pin bare boolean flags to ``True`` wherever they appear.

    ``--no-cache`` (or ``--no_cache``) becomes ``--no_cache=True``, so the flag
    works before or after the subcommand. Arguments after a standalone ``--``
    (Fire's own flags such as ``--help``) are left alone.

    Args:
        argv (list[str]): The arguments, without the program name.
        flags (Iterable[str]): Flag names with underscores, such as
            ``"no_cache"``.

    Returns:
        list[str]: A new argument list.
    """
    spellings: dict[str, str] = {}
    for flag in flags:
        bound = f"--{flag}=True"
        spellings[f"--{flag}"] = bound
        spellings[f"--{flag.replace('_', '-')}"] = bound
    out: list[str] = []
    for i, arg in enumerate(argv):
        if arg == "--":
            return out + argv[i:]
        out.append(spellings.get(arg, arg))
    return out


#: A quoted string literal (group 1, left alone) or a bare word that starts with
#: a zero followed by a digit (group 2), such as the code ``01``.
_LEADING_ZERO_WORD = re.compile(
    r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(?<![\w.])(0\d[\w.]*)"""
)


def literal_keeping_codes(value: str) -> object:
    """
    Parse a Fire argument, keeping leading-zero codes as text.

    Fire reads an argument as a Python literal with bare words allowed
    (``{stateid: CO}``), but ``{generatorid: 01}`` is not valid Python, so Fire
    passes the whole text on as a string, and ``00`` becomes the int ``0``.
    Such words are quoted first, so they arrive as ``"01"`` and ``"00"``.
    Quoted values and other numbers parse as before.

    Args:
        value (str): The argument's raw command-line text.

    Returns:
        object: The parsed value.
    """
    from fire import parser

    quoted = _LEADING_ZERO_WORD.sub(
        lambda match: match.group(1) or repr(match.group(2)), value
    )
    return parser.DefaultParseValue(quoted)


def set_parse_fns(cli: type, parsers: Mapping[str, Callable[[str], object]]) -> None:
    """
    Register Fire parse functions on every command that takes those parameters.

    Fire stores the setting on the method itself, so calling this again is
    harmless.

    Args:
        cli (type): The CLI class.
        parsers (Mapping[str, Callable[[str], object]]): Parameter name to
            parse function.
    """
    from fire import decorators

    for name in dir(cli):
        command = getattr(cli, name)
        if name.startswith("_") or not inspect.isfunction(command):
            continue
        params = inspect.signature(command).parameters
        if chosen := {k: fn for k, fn in parsers.items() if k in params}:
            decorators.SetParseFns(**chosen)(command)


def run(
    cli: type,
    *,
    name: str,
    logger: logging.Logger,
    expected: tuple[type[BaseException], ...],
    argv: list[str] | None = None,
    bool_flags: Iterable[str] = (),
) -> None:
    """
    Run a Fire CLI, turning expected errors into a short message and exit 1.

    Args:
        cli (type): The CLI class (Fire instantiates it with the global flags).
        name (str): The program name shown in help.
        logger (logging.Logger): Receives the traceback at DEBUG.
        expected (tuple[type[BaseException], ...]): Errors shown as
            ``Error: <message>`` on stderr instead of a traceback.
        argv (list[str] | None): Arguments without the program name;
            ``sys.argv[1:]`` when ``None``.
        bool_flags (Iterable[str]): Boolean flags to pin with
            :func:`bind_bool_flags`.

    Raises:
        SystemExit: With code 1 after an expected error; Fire also raises it
            for usage errors.
    """
    import fire

    args = list(sys.argv[1:] if argv is None else argv)
    try:
        fire.Fire(cli, name=name, command=bind_bool_flags(args, bool_flags))
    except expected as exc:
        logger.debug("Command failed", exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)  # ruff: ignore[print]
        raise SystemExit(1) from exc
