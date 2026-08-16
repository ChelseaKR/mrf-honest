"""Test doubles for the robots.txt gate.

``fetch_url`` requires a :class:`Politeness`, so every test has to say what robots.txt the
host serves. That is deliberate: the previous arrangement, where politeness lived in an
operator's habits rather than in the code, is exactly what the gate replaces, and a test that
could quietly skip it would recreate the problem in miniature.

These doubles serve a real robots.txt body through the real retrieval path rather than
short-circuiting the gate, so the tests that use them still exercise it.
"""

from __future__ import annotations

from urllib.request import Request

from mrf_honest.politeness import Politeness
from mrf_honest.types import ResponseLike

ALLOW_ALL = "User-agent: *\nAllow: /\n"


class RobotsResponse:
    """A minimal ResponseLike carrying one robots.txt body."""

    def __init__(self, body: str, *, url: str, status: int = 200) -> None:
        self.body = body.encode("utf-8")
        self.status = status
        self.headers: dict[str, str] = {"Content-Type": "text/plain"}
        self._url = url
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self.closed = True


class RobotsOpener:
    """Serves one robots.txt body to every origin, and refuses anything else.

    Refusing non-robots URLs is the point: if a test's Politeness object were ever asked to
    fetch the target file, that would mean the gate had been wired past the real fetcher, and
    the test should fail loudly rather than pass quietly.
    """

    def __init__(self, body: str = ALLOW_ALL, *, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.requests: list[str] = []

    def __call__(self, request: Request, *, timeout: float) -> ResponseLike:
        url = request.full_url
        if not url.endswith("/robots.txt"):
            raise AssertionError(f"the robots opener was asked for a non-robots URL: {url}")
        self.requests.append(url)
        return RobotsResponse(self.body, url=url, status=self.status)


def politeness(body: str = ALLOW_ALL, *, min_interval_seconds: float = 0.0) -> Politeness:
    """A Politeness that consults a stub robots.txt and never really sleeps."""
    return Politeness(
        user_agent="mrf-honest/0.1 (mailto:ops@example.test)",
        opener=RobotsOpener(body),
        min_interval_seconds=min_interval_seconds,
        sleep=lambda _seconds: None,
    )
