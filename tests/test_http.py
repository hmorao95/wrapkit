"""Tests for :mod:`wrapkit.http` (no network: sessions are fakes)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import pytest
import requests

from wrapkit import http


class _Resp:
    """A minimal stand-in for a requests or httpx response."""

    def __init__(self, status: int, retry_after: str | None = None) -> None:
        """
        Build the response.

        Args:
            status (int): HTTP status.
            retry_after (str | None): ``Retry-After`` header value.
        """
        self.status_code = status
        self.headers = {"Retry-After": retry_after} if retry_after else {}


class _Session:
    """Replays a script of responses and exceptions."""

    def __init__(self, script: list[Any]) -> None:
        """
        Keep the script.

        Args:
            script (list[Any]): Responses to return or exceptions to raise.
        """
        self.script = script
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        """
        Return or raise the next scripted item.

        Args:
            url (str): The URL.
            **kwargs (Any): Recorded keyword arguments.

        Returns:
            Any: The next response.

        Raises:
            Exception: The next scripted exception.
        """
        self.calls.append((url, kwargs))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _AsyncSession(_Session):
    """Async twin of :class:`_Session`."""

    async def get(self, url: str, **kwargs: Any) -> Any:
        """
        Return or raise the next scripted item.

        Args:
            url (str): The URL.
            **kwargs (Any): Recorded keyword arguments.

        Returns:
            Any: The next response.
        """
        return super().get(url, **kwargs)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """
    Record sleeps (sync and async) instead of sleeping.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patches ``time`` and ``asyncio``.

    Returns:
        list[float]: The recorded waits.
    """
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)

    async def _asleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _asleep)
    return waits


EXACT = http.RetryPolicy(jitter=False)


def test_wait_backoff_doubles_and_caps() -> None:
    """Waits double from the backoff and stop at the cap."""
    assert [EXACT.wait(a) for a in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]
    assert EXACT.wait(10) == 30.0


def test_wait_retry_after() -> None:
    """A numeric Retry-After on 429/503 wins, capped; others fall back."""
    assert EXACT.wait(1, 429, "5") == 5.0
    assert EXACT.wait(1, 503, "120") == 30.0
    assert EXACT.wait(2, 429, "Wed, 21 Oct 2015 07:28:00 GMT") == 2.0
    assert EXACT.wait(1, 429, "nan") == 1.0
    assert EXACT.wait(1, 500, "5") == 1.0


def test_wait_jitter_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jitter multiplies the backoff."""
    monkeypatch.setattr(http, "_jitter", lambda: 1.2)
    assert http.RetryPolicy().wait(2) == pytest.approx(2.4)
    assert 0.8 <= http._jitter() <= 1.2


def test_get_retries_status_then_succeeds(
    sleeps: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    """A 503 is retried, logged with the label only, then the 200 returned."""
    session: Any = _Session([_Resp(503, "2"), _Resp(200)])
    lg = logging.getLogger("wrapkit_http_test")
    with caplog.at_level(logging.DEBUG, logger="wrapkit_http_test"):
        resp = http.get_with_retries(
            session,
            "https://h/x?api_key=SECRET",
            policy=EXACT,
            logger=lg,
            label="x",
            params=[("a", "1")],
            timeout=5,
        )
    assert resp.status_code == 200
    assert sleeps == [2.0]
    assert session.calls[0][1] == {"params": [("a", "1")], "timeout": 5}
    assert "Retrying GET x in 2.0s (attempt 2/5): HTTP 503" in caplog.text
    assert "SECRET" not in caplog.text


def test_get_returns_last_bad_status(sleeps: list[float]) -> None:
    """When retries run out, the last response is returned as is."""
    policy = http.RetryPolicy(max_retries=1, jitter=False)
    session: Any = _Session([_Resp(500), _Resp(502)])
    resp = http.get_with_retries(session, "u", policy=policy)
    assert resp.status_code == 502
    assert sleeps == [1.0]


def test_get_non_retry_status_returns_at_once(sleeps: list[float]) -> None:
    """A 404 is not retried."""
    session: Any = _Session([_Resp(404)])
    assert http.get_with_retries(session, "u").status_code == 404
    assert sleeps == []


def test_get_retries_transport_error(sleeps: list[float]) -> None:
    """A connection error is retried; the default policy jitters the wait."""
    session: Any = _Session([requests.ConnectionError("down"), _Resp(200)])
    assert http.get_with_retries(session, "u").status_code == 200
    assert len(sleeps) == 1
    assert 0.8 <= sleeps[0] <= 1.2


def test_get_raises_unretryable_error(sleeps: list[float]) -> None:
    """An invalid URL fails at once."""
    session: Any = _Session([requests.exceptions.InvalidURL("bad")])
    with pytest.raises(requests.exceptions.InvalidURL):
        http.get_with_retries(session, "u")
    assert sleeps == []


def test_get_raises_after_retries(sleeps: list[float]) -> None:
    """A persistent timeout is raised once retries are used up."""
    policy = http.RetryPolicy(max_retries=2, jitter=False)
    session: Any = _Session([requests.Timeout("t")] * 3)
    with pytest.raises(requests.Timeout):
        http.get_with_retries(session, "u", policy=policy)
    assert sleeps == [1.0, 2.0]


def test_get_waits_on_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The throttle runs before every attempt."""
    calls: list[str] = []
    throttle = http.Throttle(None)
    monkeypatch.setattr(throttle, "wait", lambda: calls.append("wait"))
    session: Any = _Session([_Resp(200)])
    http.get_with_retries(session, "u", throttle=throttle)
    assert calls == ["wait"]


def test_throttle_spaces_requests(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The first request never waits; a close second sleeps the shortfall."""
    clock = {"t": 1000.0}
    sleeps: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(time, "sleep", _sleep)
    lg = logging.getLogger("wrapkit_throttle_test")
    throttle = http.Throttle(10.0, lg)
    with caplog.at_level(logging.DEBUG, logger="wrapkit_throttle_test"):
        throttle.wait()
        throttle.wait()
        clock["t"] += 1.0
        throttle.wait()
    assert sleeps == [pytest.approx(0.1)]
    assert "Throttling: sleeping 0.100s" in caplog.text


def test_throttle_disabled(sleeps: list[float]) -> None:
    """No rate means no sleeps."""
    throttle = http.Throttle(None)
    throttle.wait()
    throttle.wait()
    assert sleeps == []


def test_async_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The async throttle sleeps the shortfall without blocking."""
    clock = {"t": 50.0}
    sleeps: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    real_sleep = asyncio.sleep

    async def _asleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _asleep)
    throttle = http.AsyncThrottle(4.0)
    off = http.AsyncThrottle(None)

    async def _run() -> None:
        await throttle.await_turn()
        await throttle.await_turn()
        await off.await_turn()

    asyncio.run(_run())
    assert sleeps == [pytest.approx(0.25)]


def test_aget_retries(sleeps: list[float], caplog: pytest.LogCaptureFixture) -> None:
    """The async loop retries a 429 and a network error, then returns."""
    session: Any = _AsyncSession([_Resp(429, "3"), httpx.ConnectError("x"), _Resp(200)])
    lg = logging.getLogger("wrapkit_ahttp_test")
    throttle = http.AsyncThrottle(None)
    with caplog.at_level(logging.DEBUG, logger="wrapkit_ahttp_test"):
        resp = asyncio.run(
            http.aget_with_retries(
                session,
                "u",
                policy=EXACT,
                throttle=throttle,
                logger=lg,
                params={"a": 1},
            )
        )
    assert resp.status_code == 200
    assert sleeps == [3.0, 2.0]
    assert "ConnectError" in caplog.text
    assert session.calls[0] == ("u", {"params": {"a": 1}})


def test_aget_raises(sleeps: list[float]) -> None:
    """Unretryable and persistent async errors are raised."""
    bad: Any = _AsyncSession([httpx.UnsupportedProtocol("no")])
    with pytest.raises(httpx.UnsupportedProtocol):
        asyncio.run(http.aget_with_retries(bad, "u"))
    policy = http.RetryPolicy(max_retries=1, jitter=False)
    down: Any = _AsyncSession([httpx.ReadTimeout("t"), httpx.ReadTimeout("t")])
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(http.aget_with_retries(down, "u", policy=policy))
    assert sleeps == [1.0]


def test_aget_last_status(sleeps: list[float]) -> None:
    """With retries off the first bad answer comes back."""
    policy = http.RetryPolicy(max_retries=0)
    session: Any = _AsyncSession([_Resp(503)])
    resp = asyncio.run(http.aget_with_retries(session, "u", policy=policy))
    assert resp.status_code == 503
    assert sleeps == []


def test_user_agent() -> None:
    """The User-Agent follows the browser-like pattern."""
    assert http.user_agent("x-wrapper", "1.0", "https://x.org/") == (
        "Mozilla/5.0 (compatible; x-wrapper/1.0; +https://x.org/)"
    )
