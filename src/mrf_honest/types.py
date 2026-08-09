"""Small dependency-free value types shared across retrieval, inspection, and modeling."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublisherRef:
    """Stable publisher identity supplied by an operator, never guessed from a filename."""

    identifier: str
    name: str | None = None
    source_url: str | None = None
