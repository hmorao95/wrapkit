"""
Logging for the wrappers: a quiet library logger, CLI handlers, secret redaction.

A wrapper, as a library, only emits records on its package logger
(:func:`package_logger`); nothing is printed until an application or the
wrapper's CLI attaches handlers. The CLI calls :func:`configure_logging`, which
adds a colored console handler on stderr and, optionally, a plain-text DEBUG
log file.

API keys often travel in the query string, and both HTTP clients log full URLs
(``urllib3`` at DEBUG, ``httpx`` at INFO). A :class:`RedactFilter` installed on
those loggers masks the key's query parameter by pattern and every registered
secret wherever it appears, without dropping any record.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "CLI_HANDLER_FLAG",
    "HTTP_LOGGERS",
    "LOG_LEVELS",
    "PlainFormatter",
    "RedactFilter",
    "configure_logging",
    "loggable",
    "package_logger",
]

#: Levels accepted by a CLI's ``--log-level`` flag.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

#: Attribute set on the handlers :func:`configure_logging` installs, so a second
#: call replaces them instead of stacking duplicates.
CLI_HANDLER_FLAG = "_wrapkit_cli_handler"

#: Third-party loggers that write request URLs (and so any key in them).
HTTP_LOGGERS = ("urllib3.connectionpool", "urllib3.util.retry", "httpx")

# Secrets shorter than this are not masked as plain text: replacing a tiny
# string everywhere would mangle unrelated words. The query-parameter pattern
# still masks short keys inside URLs.
_MIN_SECRET_LENGTH = 8


def package_logger(name: str) -> logging.Logger:
    """
    Return a package's logger, silent until someone attaches a handler.

    Adds a :class:`logging.NullHandler` once, so the library never prints on its
    own and never triggers the "no handlers" fallback.

    Args:
        name (str): The logger name, normally the import name of the package.

    Returns:
        logging.Logger: The logger.
    """
    logger = logging.getLogger(name)
    if not any(isinstance(h, logging.NullHandler) for h in logger.handlers):
        logger.addHandler(logging.NullHandler())
    return logger


class RedactFilter(logging.Filter):
    """
    Remove secrets from log records.

    Each record's message is rewritten with ``***`` in place of the value of
    any of the given query parameters (``api_key=<value>``) and of every
    registered secret. Records are never dropped, so applications keep their
    HTTP logs, minus the secret. The cleaned text is frozen into the record, so
    later formatting cannot put the secret back.
    """

    def __init__(self, params: Iterable[str] = ()) -> None:
        """
        Build a filter that masks the given query parameters.

        Args:
            params (Iterable[str]): Query-parameter names whose values are
                secret, such as ``"api_key"`` or ``"token"``. The names match
                at a word boundary, so ``key`` does not match inside
                ``api_key``.
        """
        super().__init__()
        self.secrets: set[str] = set()
        names = sorted(set(params))
        self._pattern = (
            re.compile(rf"(\b(?:{'|'.join(map(re.escape, names))})=)[^&\s\"']+")
            if names
            else None
        )

    def register(self, secret: str | None) -> None:
        """
        Mask ``secret`` wherever it appears in a record.

        Args:
            secret (str | None): The secret; ``None`` or a value shorter than
                eight characters is ignored.
        """
        if secret and len(secret) >= _MIN_SECRET_LENGTH:
            self.secrets.add(secret)

    def redact(self, text: str) -> str:
        """
        Return ``text`` with every secret masked.

        Also useful outside logging, for example on an exception message that
        quotes the request URL.

        Args:
            text (str): Any text.

        Returns:
            str: The text with secret parameter values and registered secrets
            replaced by ``***``.
        """
        if self._pattern is not None:
            text = self._pattern.sub(r"\g<1>***", text)
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Redact secrets from a record's message.

        Args:
            record (logging.LogRecord): The record about to be emitted.

        Returns:
            bool: Always ``True``: the record is kept, only its text changes.
        """
        message = record.getMessage()
        cleaned = self.redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True

    def install(self, *loggers: str) -> RedactFilter:
        """
        Attach this filter to the HTTP loggers and to the named loggers.

        Args:
            *loggers (str): Further logger names, normally the package logger,
                as a safety net.

        Returns:
            RedactFilter: This filter, so ``f = RedactFilter(...).install(...)``
            reads in one line.
        """
        for name in (*HTTP_LOGGERS, *loggers):
            logger = logging.getLogger(name)
            if self not in logger.filters:
                logger.addFilter(self)
        return self


def loggable(params: Sequence[tuple[str, str]], hidden: Iterable[str]) -> str:
    """
    Render request parameters for a log line, leaving secret ones out.

    Args:
        params (Sequence[tuple[str, str]]): Request parameters as (name, value)
            pairs.
        hidden (Iterable[str]): Names of the parameters to leave out.

    Returns:
        str: ``name=value`` pairs joined by ``&``.
    """
    skip = set(hidden)
    return "&".join(f"{k}={v}" for k, v in params if k not in skip)


class PlainFormatter(logging.Formatter):
    """
    A file formatter that always renders tracebacks without color.

    ``logging`` caches a record's formatted traceback in ``exc_text`` for the
    next handler. The colored console formatter (on Python 3.13+) renders it
    with ANSI color codes, which would then leak into the log file; this
    formatter ignores the cache and formats the traceback itself.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a record, re-rendering any traceback as plain text.

        Args:
            record (logging.LogRecord): The record to format.

        Returns:
            str: The formatted line(s).
        """
        cached, record.exc_text = record.exc_text, None
        try:
            return super().format(record)
        finally:
            record.exc_text = cached


def configure_logging(
    logger: logging.Logger,
    level: str = "WARNING",
    log_file: str | None = None,
    *,
    flag: str = CLI_HANDLER_FLAG,
) -> None:
    """
    Attach a CLI's console (and optional file) handlers to a package logger.

    Only a command line calls this; libraries never configure logging. The
    console handler writes colored records to stderr, so stdout keeps only the
    command's output and can still be piped. The file handler, when requested,
    appends every record down to DEBUG with full timestamps, whatever the
    console level. Calling it again replaces the handlers it added earlier
    (those carrying the ``flag`` attribute) rather than stacking duplicates.

    Args:
        logger (logging.Logger): The package logger.
        level (str): Console threshold, one of ``DEBUG``, ``INFO``, ``WARNING``
            or ``ERROR`` (case-insensitive).
        log_file (str | None): Path of a log file to append to, or ``None`` for
            console logging only.
        flag (str): Attribute name that marks the installed handlers.

    Raises:
        ValueError: If ``level`` is not one of the accepted names.
    """
    # Imported lazily: only a CLI needs colored output.
    import colorlog

    name = level.upper()
    if name not in LOG_LEVELS:
        msg = f"log_level must be one of {', '.join(LOG_LEVELS)}, got {level!r}."
        raise ValueError(msg)
    console_level = getattr(logging, name)

    for handler in list(logger.handlers):
        if getattr(handler, flag, False):
            logger.removeHandler(handler)
            handler.close()

    console = colorlog.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s - %(levelname)s%(reset)s %(message)s",
            datefmt="%H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
    )
    handlers: list[logging.Handler] = [console]

    if log_file:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            PlainFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        handlers.append(file_handler)

    for handler in handlers:
        setattr(handler, flag, True)
        logger.addHandler(handler)
    # The logger must pass the most verbose level any handler wants.
    logger.setLevel(logging.DEBUG if log_file else console_level)
