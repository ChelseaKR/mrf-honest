"""robots.txt, pacing and Retry-After, measured against a real server on localhost.

The unit-level cases here use the pure helpers. The end-to-end cases stand up an actual
``http.server`` on loopback and drive the real ``fetch_url`` path through it, because the
thing being asserted is that a request was *not made* -- and only a server that would have
noticed can testify to that.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest
from robots_fixtures import RobotsOpener

from mrf_honest.fetch import FetchPolicy, FetchStatus, fetch_url
from mrf_honest.politeness import (
    DEFAULT_MIN_INTERVAL_SECONDS,
    MAX_RETRY_AFTER_SECONDS,
    PRODUCT_TOKEN,
    HostPacer,
    Politeness,
    RobotsGate,
    RobotsStatus,
    parse_retry_after,
    robots_url_for,
)
from mrf_honest.types import ResponseLike

CONTACT = "ops@example.test"


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves whatever the test put in ``server.routes`` and remembers every request."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # keep pytest output readable
        return

    # BaseHTTPRequestHandler dispatches on this exact spelling.
    def do_GET(self) -> None:
        server: Any = self.server
        server.seen.append((self.path, self.headers.get("User-Agent", "")))
        route = server.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status, body, extra = route
        payload = body.encode("utf-8")
        self.send_response(status)
        for key, value in extra.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, routes: dict[str, tuple[int, str, dict[str, str]]]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.routes = routes
        self.seen: list[tuple[str, str]] = []


@pytest.fixture
def server() -> Iterator[_Server]:
    instance = _Server({})
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()


def _origin(instance: _Server) -> str:
    host, port = instance.server_address[0], instance.server_address[1]
    return f"http://{host}:{port}"


def _gate(instance: _Server) -> RobotsGate:
    from urllib.request import build_opener

    opener = build_opener()

    def open_it(request: Request, *, timeout: float) -> ResponseLike:
        return opener.open(request, timeout=timeout)  # type: ignore[return-value]

    return RobotsGate(user_agent=f"{PRODUCT_TOKEN}/0.1 (mailto:{CONTACT})", opener=open_it)


# ---------------------------------------------------------------- robots.txt, over the wire


def test_a_disallow_for_our_product_token_is_a_hard_stop(server: _Server) -> None:
    origin = _origin(server)
    server.routes["/robots.txt"] = (
        200,
        f"User-agent: {PRODUCT_TOKEN}\nDisallow: /private/\n\nUser-agent: *\nAllow: /\n",
        {"Content-Type": "text/plain"},
    )
    decision = _gate(server).decide(f"{origin}/private/prices.json")
    assert decision.status is RobotsStatus.DISALLOWED
    assert not decision.allowed
    assert "no override flag" in decision.reason
    assert decision.robots_url == robots_url_for(f"{origin}/private/prices.json")
    # and a path outside the disallow is still fine
    assert _gate(server).decide(f"{origin}/public/prices.json").allowed


def test_a_wildcard_disallow_stops_us_too(server: _Server) -> None:
    server.routes["/robots.txt"] = (200, "User-agent: *\nDisallow: /\n", {})
    decision = _gate(server).decide(f"{_origin(server)}/prices.json")
    assert decision.status is RobotsStatus.DISALLOWED


def test_a_404_means_no_robots_exists_and_the_fetch_may_proceed(server: _Server) -> None:
    """RFC 9309 section 2.3.1.3."""
    decision = _gate(server).decide(f"{_origin(server)}/prices.json")
    assert decision.status is RobotsStatus.ABSENT
    assert decision.allowed
    assert decision.http_status == 404


def test_a_500_is_a_complete_disallow(server: _Server) -> None:
    """RFC 9309 section 2.3.1.4. A robots.txt we could not read is not permission."""
    server.routes["/robots.txt"] = (503, "down", {})
    decision = _gate(server).decide(f"{_origin(server)}/prices.json")
    assert decision.status is RobotsStatus.UNREACHABLE
    assert not decision.allowed
    assert decision.http_status == 503


def test_an_unreachable_host_is_a_complete_disallow() -> None:
    gate = RobotsGate(
        user_agent=f"{PRODUCT_TOKEN}/0.1",
        opener=_refusing_opener,
        timeout_seconds=1.0,
    )
    decision = gate.decide("https://127.0.0.1:9/prices.json")
    assert decision.status is RobotsStatus.UNREACHABLE
    assert not decision.allowed


def _refusing_opener(request: Request, *, timeout: float) -> ResponseLike:
    raise OSError("connection refused")


def test_the_user_agent_carries_the_product_token_and_a_contact(server: _Server) -> None:
    """RFC 9309 section 2.2.1: the token a robots.txt author writes must be in the UA."""
    server.routes["/robots.txt"] = (200, "User-agent: *\nAllow: /\n", {})
    _gate(server).decide(f"{_origin(server)}/prices.json")
    paths = [path for path, _ in server.seen]
    agents = [agent for _, agent in server.seen]
    assert paths == ["/robots.txt"]
    assert PRODUCT_TOKEN in agents[0]
    assert CONTACT in agents[0]


def test_robots_is_fetched_once_per_origin(server: _Server) -> None:
    server.routes["/robots.txt"] = (200, "User-agent: *\nAllow: /\n", {})
    gate = _gate(server)
    for index in range(4):
        assert gate.decide(f"{_origin(server)}/file-{index}.json").allowed
    assert [path for path, _ in server.seen] == ["/robots.txt"]


def test_crawl_delay_is_read_from_robots(server: _Server) -> None:
    """RobotFileParser silently returns None for crawl_delay unless the parser is marked
    as read; this test is why ``_parse`` calls ``modified()``."""
    server.routes["/robots.txt"] = (
        200,
        f"User-agent: {PRODUCT_TOKEN}\nCrawl-delay: 17\nAllow: /\n",
        {},
    )
    decision = _gate(server).decide(f"{_origin(server)}/prices.json")
    assert decision.allowed
    assert decision.crawl_delay_seconds == 17.0


# ---------------------------------------------------------------- the fetcher obeys the gate


def test_fetch_url_makes_no_request_at_all_when_robots_disallows(
    server: _Server, tmp_path: Path
) -> None:
    """The point of the whole module: the file request is never sent."""
    server.routes["/robots.txt"] = (200, "User-agent: *\nDisallow: /\n", {})
    server.routes["/prices.json"] = (200, '{"a":1}', {"Content-Type": "application/json"})
    url = f"{_origin(server)}/prices.json"

    from urllib.request import build_opener

    opener = build_opener()

    def open_it(request: Request, *, timeout: float) -> ResponseLike:
        return opener.open(request, timeout=timeout)  # type: ignore[return-value]

    politeness = Politeness(
        user_agent=f"{PRODUCT_TOKEN}/0.1 (mailto:{CONTACT})",
        opener=open_it,
        min_interval_seconds=0.0,
        sleep=lambda _seconds: None,
    )
    # fetch_url refuses plain http before robots is even consulted, so drive the gate
    # directly and then confirm the fetcher's own status mapping.
    decision = politeness.clear_to_fetch(url)
    assert decision.status is RobotsStatus.DISALLOWED
    assert [path for path, _ in server.seen] == ["/robots.txt"]

    outcome = fetch_url(
        "https://blocked.example.test/prices.json",
        tmp_path,
        policy=FetchPolicy(contact=CONTACT),
        politeness=Politeness(
            user_agent=f"{PRODUCT_TOKEN}/0.1",
            opener=RobotsOpener("User-agent: *\nDisallow: /\n"),
            min_interval_seconds=0.0,
            sleep=lambda _seconds: None,
        ),
        opener=_never_called,
    )
    assert outcome.status is FetchStatus.ROBOTS_DISALLOWED
    assert outcome.attempts == 0
    assert "disallowed" in (outcome.error or "")


def _never_called(request: Request, *, timeout: float) -> ResponseLike:
    raise AssertionError("the file was requested despite a robots.txt disallow")


def test_an_unreadable_robots_also_stops_the_fetch(tmp_path: Path) -> None:
    politeness = Politeness(
        user_agent=f"{PRODUCT_TOKEN}/0.1",
        opener=_refusing_opener,
        min_interval_seconds=0.0,
        sleep=lambda _seconds: None,
    )
    outcome = fetch_url(
        "https://unreachable.example.test/prices.json",
        tmp_path,
        policy=FetchPolicy(contact=CONTACT),
        politeness=politeness,
        opener=_never_called,
    )
    assert outcome.status is FetchStatus.ROBOTS_DISALLOWED
    assert "unreachable" in (outcome.error or "")


def test_there_is_no_argument_that_turns_robots_off() -> None:
    """A regression guard on the API shape, not on behaviour.

    The whole control depends on there being no way to spell "skip robots". If someone adds
    an ``ignore_robots`` or ``force`` parameter later, this fails.
    """
    import inspect

    names = set(inspect.signature(fetch_url).parameters)
    assert "politeness" in names
    assert not {"ignore_robots", "skip_robots", "force", "no_robots"} & names
    assert fetch_url.__kwdefaults__ is None or "politeness" not in (
        fetch_url.__kwdefaults__ or {}
    ), "politeness must be required; a default is a bypass"


# ---------------------------------------------------------------------------------- pacing


def test_the_interval_is_held_between_requests_to_one_host() -> None:
    slept: list[float] = []
    clock = iter([0.0, 0.5])
    pacer = HostPacer(min_interval_seconds=2.0, sleep=slept.append, monotonic=lambda: next(clock))
    first = pacer.wait_turn("a.example")
    assert first.waited_seconds == 0.0 and first.reason == "no_wait"
    second = pacer.wait_turn("a.example")
    assert second.waited_seconds == pytest.approx(1.5)
    assert second.reason == "min_interval"
    assert slept == [pytest.approx(1.5)]


def test_a_different_host_does_not_wait() -> None:
    slept: list[float] = []
    clock = iter([0.0, 0.1, 0.2])
    pacer = HostPacer(min_interval_seconds=5.0, sleep=slept.append, monotonic=lambda: next(clock))
    pacer.wait_turn("a.example")
    assert pacer.wait_turn("b.example").waited_seconds == 0.0
    assert slept == []


def test_crawl_delay_lengthens_the_interval_and_never_shortens_it() -> None:
    slept: list[float] = []
    clock = iter([0.0, 0.0, 0.0, 0.0])
    pacer = HostPacer(min_interval_seconds=2.0, sleep=slept.append, monotonic=lambda: next(clock))
    pacer.wait_turn("a.example", crawl_delay_seconds=10.0)
    pacer.wait_turn("a.example", crawl_delay_seconds=10.0)
    assert slept == [pytest.approx(10.0)]

    slept.clear()
    clock2 = iter([0.0, 0.0, 0.0, 0.0])
    lax = HostPacer(min_interval_seconds=5.0, sleep=slept.append, monotonic=lambda: next(clock2))
    lax.wait_turn("b.example", crawl_delay_seconds=0.1)
    lax.wait_turn("b.example", crawl_delay_seconds=0.1)
    assert slept == [pytest.approx(5.0)], "a short Crawl-delay must not shorten our floor"


def test_the_default_interval_is_not_zero() -> None:
    assert DEFAULT_MIN_INTERVAL_SECONDS > 0
    assert HostPacer().min_interval_seconds == DEFAULT_MIN_INTERVAL_SECONDS


def test_a_negative_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        HostPacer(min_interval_seconds=-1.0)


# ----------------------------------------------------------------------------- Retry-After


def test_retry_after_parses_delay_seconds_and_http_dates() -> None:
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    assert parse_retry_after("120", now=now) == 120.0
    assert parse_retry_after("  30 ", now=now) == 30.0
    later = (now + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(later, now=now) == pytest.approx(90.0, abs=1.0)
    earlier = (now - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(earlier, now=now) == 0.0, "a past date means zero, never negative"


def test_an_unreadable_retry_after_is_none_not_zero() -> None:
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    assert parse_retry_after(None, now=now) is None
    assert parse_retry_after("", now=now) is None
    assert parse_retry_after("soon please", now=now) is None
    assert parse_retry_after("12.5", now=now) is None


def test_retry_after_is_honoured_only_on_429_and_503() -> None:
    politeness = Politeness(
        user_agent=f"{PRODUCT_TOKEN}/0.1",
        opener=RobotsOpener(),
        min_interval_seconds=0.0,
        sleep=lambda _seconds: None,
    )
    url = "https://example.test/prices.json"
    assert politeness.observe_retry_after(url, 429, {"Retry-After": "45"}) == 45.0
    assert politeness.observe_retry_after(url, 503, {"retry-after": "7"}) == 7.0
    assert politeness.observe_retry_after(url, 500, {"Retry-After": "45"}) is None
    assert politeness.observe_retry_after(url, 200, {"Retry-After": "45"}) is None
    assert politeness.observe_retry_after(url, 429, {}) is None


def test_an_absurd_retry_after_is_capped() -> None:
    politeness = Politeness(
        user_agent=f"{PRODUCT_TOKEN}/0.1",
        opener=RobotsOpener(),
        min_interval_seconds=0.0,
        sleep=lambda _seconds: None,
    )
    asked = politeness.observe_retry_after(
        "https://example.test/a.json", 503, {"Retry-After": "999999"}
    )
    assert asked == MAX_RETRY_AFTER_SECONDS


def test_the_fetcher_waits_what_the_server_asked_rather_than_its_own_backoff(
    tmp_path: Path,
) -> None:
    from test_fetch import FakeOpener, FakeResponse, clock, policy

    slept: list[float] = []
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        policy=policy(retries=1, backoff_seconds=100.0),
        politeness=Politeness(
            user_agent=f"{PRODUCT_TOKEN}/0.1",
            opener=RobotsOpener(),
            min_interval_seconds=0.0,
            sleep=lambda _seconds: None,
        ),
        opener=FakeOpener(
            FakeResponse(status=503, headers={"Retry-After": "3"}),
            FakeResponse(b'{"ok":true}'),
        ),
        sleep=slept.append,
        clock=clock,
    )
    assert outcome.status is FetchStatus.FETCHED
    assert slept == [3.0], "the server said 3 seconds; the tool's own 100s backoff must yield"


# ----------------------------------------------------------------------------- the evidence


def test_every_decision_and_wait_is_retained_as_evidence(server: _Server) -> None:
    server.routes["/robots.txt"] = (200, "User-agent: *\nCrawl-delay: 1\nAllow: /\n", {})

    from urllib.request import build_opener

    opener = build_opener()

    def open_it(request: Request, *, timeout: float) -> ResponseLike:
        return opener.open(request, timeout=timeout)  # type: ignore[return-value]

    politeness = Politeness(
        user_agent=f"{PRODUCT_TOKEN}/0.1",
        opener=open_it,
        min_interval_seconds=0.0,
        sleep=lambda _seconds: None,
    )
    url = f"{_origin(server)}/prices.json"
    decision = politeness.clear_to_fetch(url)
    politeness.wait_turn(url, crawl_delay_seconds=decision.crawl_delay_seconds)
    politeness.wait_turn(url, crawl_delay_seconds=decision.crawl_delay_seconds)

    evidence = politeness.evidence()
    assert evidence["product_token"] == PRODUCT_TOKEN
    robots = evidence["robots"]
    pacing = evidence["pacing"]
    assert isinstance(robots, list) and isinstance(pacing, list)
    assert len(robots) == 1
    assert robots[0]["allowed"] is True
    assert robots[0]["crawl_delay_seconds"] == 1.0
    assert robots[0]["checked_at"].startswith("20")
    assert len(pacing) == 2
    assert pacing[0]["reason"] == "no_wait"
    assert pacing[1]["reason"] == "crawl_delay"
