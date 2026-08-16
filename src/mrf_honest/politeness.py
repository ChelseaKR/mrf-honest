"""robots.txt, per-host pacing, and ``Retry-After``, enforced in code.

Until now this project achieved politeness by procedure: an operator checked four
``robots.txt`` files by hand on 2026-08-13 and recorded the result in the cohort manifest.
That is a good attestation and a bad control -- it is a person remembering, and the manifest
says what was done rather than the fetcher preventing what was not. `docs/ROADMAP.md` named
the gap as a scope limit on any broad or scheduled retrieval, and this module closes it.

Three rules, and none of them has an off switch:

**robots.txt is fetched first and obeyed.** There is no flag to skip it, because a flag to
ignore robots.txt is the whole of the harm. Following RFC 9309:

* section 2.3.1.1 -- a 2xx body is parsed and applied;
* section 2.3.1.2 -- redirects are followed up to five hops, and a sixth is treated as
  unreachable;
* section 2.3.1.3 -- a 4xx means no robots.txt exists and the fetch may proceed;
* section 2.3.1.4 -- 5xx and any network failure mean *unreachable*, which is a complete
  disallow. A robots.txt this tool could not read is not permission.

**A per-host minimum interval, held across a whole run.** A ``Crawl-delay`` in robots.txt can
only lengthen it, never shorten it. The interval is enforced between requests to the same
host rather than per invocation, which is the difference between a paced run and a serial
operator who happened to be slow.

**``Retry-After`` is honoured on 429 and 503, and the wait is recorded.** Retry backoff is a
policy this tool chose; ``Retry-After`` is the server saying what it wants. For hospital file
hosts serving multi-hundred-megabyte JSON that distinction is not academic.

Every decision and every wait is evidence: :class:`RobotsDecision` and :class:`PacingRecord`
are JSON-safe and belong in the registry alongside the fetch outcome, so a future cohort
manifest can record what the fetcher did rather than what a person did.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from http.client import HTTPException, InvalidURL
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request
from urllib.robotparser import RobotFileParser

from mrf_honest.types import Clock, Opener, ResponseLike, Sleeper

PRODUCT_TOKEN = "mrf-honest"  # noqa: S105 - a robots.txt product token, not a secret
"""The product token robots.txt authors match on, per RFC 9309 section 2.2.1."""

DEFAULT_MIN_INTERVAL_SECONDS = 2.0
"""Minimum gap between two requests to the same host. Crawl-delay may lengthen this."""

MAX_ROBOTS_BYTES = 512 * 1024
"""RFC 9309 section 2.5 requires parsing at least 500 KiB; nothing beyond that is read."""

MAX_ROBOTS_REDIRECTS = 5
"""RFC 9309 section 2.3.1.2."""

RETRY_AFTER_STATUSES = frozenset({429, 503})

MAX_RETRY_AFTER_SECONDS = 3600.0
"""A server asking for longer than an hour ends the attempt rather than parking a process."""


_ROBOTS_TRANSPORT_ERRORS = (
    HTTPException,
    URLError,
    InvalidURL,
    TimeoutError,
    OSError,
    ValueError,
)


class RobotsStatus(StrEnum):
    """Why a URL was allowed or refused, in the vocabulary of RFC 9309 section 2.3.1."""

    ALLOWED = "allowed"
    DISALLOWED = "disallowed"
    ABSENT = "absent"  # 4xx: no robots.txt exists, so everything is allowed (2.3.1.3)
    UNREACHABLE = "unreachable"  # 5xx or network failure: complete disallow (2.3.1.4)


_RobotsCacheEntry = tuple["RobotsStatus", "RobotFileParser | None", str, int | None]
"""What one origin's robots.txt resolved to: status, parser, reason, HTTP status."""


@dataclass(frozen=True)
class RobotsDecision:
    """One retained, dated decision about one URL."""

    url: str
    robots_url: str
    status: RobotsStatus
    reason: str
    checked_at: str
    crawl_delay_seconds: float | None = None
    http_status: int | None = None

    @property
    def allowed(self) -> bool:
        return self.status in {RobotsStatus.ALLOWED, RobotsStatus.ABSENT}

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "robots_url": self.robots_url,
            "status": self.status.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "checked_at": self.checked_at,
            "crawl_delay_seconds": self.crawl_delay_seconds,
            "http_status": self.http_status,
        }


@dataclass(frozen=True)
class PacingRecord:
    """How long this request waited before it was made, and on whose instruction."""

    host: str
    waited_seconds: float
    reason: str  # "no_wait" | "min_interval" | "crawl_delay" | "retry_after"

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "waited_seconds": round(self.waited_seconds, 3),
            "reason": self.reason,
        }


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def robots_url_for(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


def parse_retry_after(value: str | None, *, now: datetime) -> float | None:
    """Seconds to wait per RFC 9110 section 10.2.3, as delay-seconds or an HTTP-date.

    Returns ``None`` when the header is absent or unparseable -- never a silent zero, because
    "I could not read the instruction" and "the server said go now" are different facts.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(int(text))
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - now).total_seconds()
    return max(0.0, seconds)


class HostPacer:
    """Minimum interval per host, held for the lifetime of the pacer."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        sleep: Sleeper = time.sleep,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        self.min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._monotonic: Callable[[], float] = monotonic or time.monotonic
        self._next_allowed: dict[str, float] = {}
        self._reasons: dict[str, str] = {}

    def _interval(self, crawl_delay_seconds: float | None) -> tuple[float, str]:
        if crawl_delay_seconds is not None and crawl_delay_seconds > self.min_interval_seconds:
            return crawl_delay_seconds, "crawl_delay"
        return self.min_interval_seconds, "min_interval"

    def wait_turn(self, host: str, *, crawl_delay_seconds: float | None = None) -> PacingRecord:
        """Block until this host may be contacted again, and say why it waited."""
        now = self._monotonic()
        due = self._next_allowed.get(host)
        waited = 0.0
        reason = "no_wait"
        if due is not None and due > now:
            waited = due - now
            reason = self._reasons.get(host, "min_interval")
            self._sleep(waited)
            now = due
        interval, next_reason = self._interval(crawl_delay_seconds)
        self._next_allowed[host] = now + interval
        self._reasons[host] = next_reason
        return PacingRecord(host=host, waited_seconds=waited, reason=reason)

    def defer(self, host: str, seconds: float, *, reason: str = "retry_after") -> None:
        """Push this host's next allowed time out, never in.

        A server asking for a longer wait than the pacer already planned always wins; a server
        asking for a shorter one does not shorten it.
        """
        now = self._monotonic()
        proposed = now + max(0.0, seconds)
        current = self._next_allowed.get(host, now)
        if proposed > current:
            self._next_allowed[host] = proposed
            self._reasons[host] = reason


class RobotsGate:
    """Fetches, caches and applies one robots.txt per origin."""

    def __init__(
        self,
        *,
        user_agent: str,
        opener: Opener,
        timeout_seconds: float = 30.0,
        clock: Clock | None = None,
        preloaded: Mapping[str, str] | None = None,
    ) -> None:
        self.user_agent = user_agent
        self._opener: Opener = opener
        self.timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache: dict[str, _RobotsCacheEntry] = {}
        for origin, text in (preloaded or {}).items():
            self._cache[origin] = (RobotsStatus.ALLOWED, _parse(text), "preloaded body", 200)

    def _timestamp(self) -> str:
        return self._clock().astimezone(UTC).replace(microsecond=0).isoformat()

    def _retrieve(
        self, origin: str
    ) -> tuple[RobotsStatus, RobotFileParser | None, str, int | None]:
        target = f"{origin}/robots.txt"
        seen = 0
        while True:
            try:
                request = Request(target, headers={"User-Agent": self.user_agent})  # noqa: S310
                response: ResponseLike = self._opener(request, timeout=self.timeout_seconds)
            except HTTPError as error:
                code = error.code
                error.close()
                if 400 <= code < 500:
                    return (
                        RobotsStatus.ABSENT,
                        None,
                        f"HTTP {code}: no robots.txt (RFC 9309 2.3.1.3)",
                        code,
                    )
                return (
                    RobotsStatus.UNREACHABLE,
                    None,
                    f"HTTP {code}: robots.txt unreachable, treated as a complete disallow "
                    "(RFC 9309 2.3.1.4)",
                    code,
                )
            except (
                HTTPException,
                URLError,
                InvalidURL,
                TimeoutError,
                OSError,
                ValueError,
            ) as error:
                return (
                    RobotsStatus.UNREACHABLE,
                    None,
                    f"robots.txt could not be retrieved ({error}); a robots.txt this tool could "
                    "not read is not permission (RFC 9309 2.3.1.4)",
                    None,
                )
            try:
                status = int(response.status or 200)
                final = response.geturl()
                if status in {301, 302, 303, 307, 308} or (final != target and seen == 0):
                    # urllib normally follows redirects itself; this branch covers openers that
                    # hand them back. Either way the hop budget is bounded.
                    seen += 1
                    if seen > MAX_ROBOTS_REDIRECTS:
                        return (
                            RobotsStatus.UNREACHABLE,
                            None,
                            f"more than {MAX_ROBOTS_REDIRECTS} redirects (RFC 9309 2.3.1.2)",
                            status,
                        )
                    if final != target and urlsplit(final).scheme == "https":
                        target = final
                        continue
                if 400 <= status < 500:
                    return RobotsStatus.ABSENT, None, f"HTTP {status}: no robots.txt", status
                if status >= 500:
                    return (
                        RobotsStatus.UNREACHABLE,
                        None,
                        f"HTTP {status}: robots.txt unreachable, complete disallow",
                        status,
                    )
                body = response.read(MAX_ROBOTS_BYTES)
            finally:
                response.close()
            return (
                RobotsStatus.ALLOWED,
                _parse(body.decode("utf-8", errors="replace")),
                f"HTTP {status}: robots.txt retrieved and applied",
                status,
            )

    def decide(self, url: str) -> RobotsDecision:
        """Whether ``url`` may be fetched, with the reason and the crawl delay."""
        origin = origin_of(url)
        cached = self._cache.get(origin)
        if cached is None:
            cached = self._retrieve(origin)
            self._cache[origin] = cached
        outcome, parser, reason, http_status = cached
        robots_url = robots_url_for(url)
        if outcome is not RobotsStatus.ALLOWED or parser is None:
            return RobotsDecision(
                url=url,
                robots_url=robots_url,
                status=outcome,
                reason=reason,
                checked_at=self._timestamp(),
                http_status=http_status,
            )
        delay = parser.crawl_delay(self.user_agent)
        crawl_delay = float(delay) if delay is not None else None
        if not parser.can_fetch(self.user_agent, url):
            return RobotsDecision(
                url=url,
                robots_url=robots_url,
                status=RobotsStatus.DISALLOWED,
                reason=(
                    f"robots.txt disallows this path for {PRODUCT_TOKEN}; there is no override "
                    "flag, because a flag to ignore robots.txt is the whole of the harm"
                ),
                checked_at=self._timestamp(),
                crawl_delay_seconds=crawl_delay,
                http_status=http_status,
            )
        return RobotsDecision(
            url=url,
            robots_url=robots_url,
            status=RobotsStatus.ALLOWED,
            reason=reason,
            checked_at=self._timestamp(),
            crawl_delay_seconds=crawl_delay,
            http_status=http_status,
        )


def _parse(text: str) -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    # RobotFileParser.crawl_delay() returns None unless the parser has been marked as read;
    # without this a declared Crawl-delay is silently discarded. Found by test, not by reading.
    parser.modified()
    return parser


@dataclass
class Politeness:
    """robots.txt plus pacing plus Retry-After, as one object with no bypass.

    ``fetch_url`` requires one of these. It is a required argument rather than an optional one
    precisely so that no call site can retrieve anything without having made the decision --
    the previous arrangement, where politeness lived in an operator's habits, is what this
    replaces.
    """

    user_agent: str
    opener: Opener
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS
    timeout_seconds: float = 30.0
    sleep: Sleeper = time.sleep
    clock: Clock | None = None
    preloaded_robots: Mapping[str, str] | None = None
    monotonic: Callable[[], float] | None = None
    decisions: list[RobotsDecision] = field(default_factory=list)
    waits: list[PacingRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._gate = RobotsGate(
            user_agent=self.user_agent,
            opener=self.opener,
            timeout_seconds=self.timeout_seconds,
            clock=self.clock,
            preloaded=self.preloaded_robots,
        )
        self._pacer = HostPacer(
            min_interval_seconds=self.min_interval_seconds,
            sleep=self.sleep,
            monotonic=self.monotonic,
        )

    def clear_to_fetch(self, url: str) -> RobotsDecision:
        """Consult robots.txt for ``url`` and retain the decision."""
        decision = self._gate.decide(url)
        self.decisions.append(decision)
        return decision

    def wait_turn(self, url: str, *, crawl_delay_seconds: float | None = None) -> PacingRecord:
        record = self._pacer.wait_turn(
            urlsplit(url).netloc, crawl_delay_seconds=crawl_delay_seconds
        )
        self.waits.append(record)
        return record

    def observe_retry_after(
        self, url: str, http_status: int, headers: Mapping[str, str]
    ) -> float | None:
        """Honour a server's ``Retry-After`` on 429/503; return the seconds it asked for."""
        if http_status not in RETRY_AFTER_STATUSES:
            return None
        raw = next(
            (value for key, value in headers.items() if key.lower() == "retry-after"),
            None,
        )
        now = (self.clock or (lambda: datetime.now(UTC)))().astimezone(UTC)
        seconds = parse_retry_after(raw, now=now)
        if seconds is None:
            return None
        seconds = min(seconds, MAX_RETRY_AFTER_SECONDS)
        self._pacer.defer(urlsplit(url).netloc, seconds)
        return seconds

    def evidence(self) -> dict[str, object]:
        """Everything this object did, for the registry."""
        return {
            "robots": [decision.to_dict() for decision in self.decisions],
            "pacing": [record.to_dict() for record in self.waits],
            "min_interval_seconds": self.min_interval_seconds,
            "product_token": PRODUCT_TOKEN,
        }
