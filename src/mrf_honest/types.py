"""Small dependency-free value types shared across retrieval, inspection, and modeling."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.request import Request


class ResponseLike(Protocol):
    """The small part of ``urllib`` responses used by the fetcher."""

    status: int
    headers: Mapping[str, str]

    def read(self, amount: int = -1) -> bytes: ...

    def geturl(self) -> str: ...

    def close(self) -> None: ...


class Opener(Protocol):
    """Injectable network boundary; tests provide a deterministic implementation."""

    def __call__(self, request: Request, *, timeout: float) -> ResponseLike: ...


Sleeper = Callable[[float], None]
Backoff = Callable[[int], float]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class PublisherRef:
    """Stable publisher identity supplied by an operator, never guessed from a filename."""

    identifier: str
    name: str | None = None
    source_url: str | None = None
