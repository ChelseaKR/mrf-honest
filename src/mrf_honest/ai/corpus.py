"""The cited texts, indexed, and a verifier for quotes taken from them.

Every finding in the catalog cites a URL. ``corpus/SOURCES.json`` maps those
URLs to retained copies of the documents (45 CFR Part 180 as eCFR XML, the
CMS JSON and CSV data dictionaries as Markdown). This module turns them into
passages a model can be shown and, more importantly, checks that a quote the
model attributes to a document actually occurs in it. The check is a pure
function over committed files: the document is the evidence, the model is
only the narrator.
"""

from __future__ import annotations

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIN_QUOTE_CHARS = 24
PASSAGE_TARGET_CHARS = 900
PASSAGE_MAX_CHARS = 1600


class CorpusError(ValueError):
    """The corpus could not be indexed as committed."""


@dataclass(frozen=True)
class Passage:
    passage_id: str
    source_id: str
    index: int
    heading: str
    text: str


@dataclass(frozen=True)
class Document:
    source_id: str
    label: str
    citation_urls: tuple[str, ...]
    local_copy: str
    sections: tuple[tuple[str, str], ...]
    passages: tuple[Passage, ...]
    normalized: str


@dataclass(frozen=True)
class QuoteMatch:
    source_id: str
    quote: str
    passage_id: str | None


_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",  # left single quotation mark
        "\u2019": "'",  # right single quotation mark
        "\u201c": '"',  # left double quotation mark
        "\u201d": '"',  # right double quotation mark
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u00a0": " ",  # no-break space
        "\u00ad": "",  # soft hyphen
    }
)


def normalize_for_match(text: str) -> str:
    """Reduce text to the characters that carry meaning for a verbatim check.

    NFKC-folded, typographic quotes and dashes straightened, case folded,
    and everything that is not a letter, digit, or section sign removed, so
    that line breaks, markdown emphasis, and punctuation spacing cannot make
    a faithful quote fail or a changed quote pass.
    """
    folded = unicodedata.normalize("NFKC", text).translate(_QUOTE_MAP).casefold()
    return "".join(ch for ch in folded if ch.isalnum() or ch == "§")


def ecfr_sections(xml_text: str) -> list[tuple[str, str]]:
    """(heading, body) per section of an eCFR part, in document order."""
    root = ET.fromstring(xml_text)  # noqa: S314 - committed file, no external entities
    sections: list[tuple[str, str]] = []
    for section in root.iter("DIV8"):
        head = section.find("HEAD")
        heading = " ".join("".join(head.itertext()).split()) if head is not None else ""
        paragraphs = [" ".join("".join(p.itertext()).split()) for p in section.iter("P")]
        body = "\n\n".join(p for p in paragraphs if p)
        if heading or body:
            sections.append((heading, body))
    if not sections:
        raise CorpusError("eCFR document has no DIV8 sections")
    return sections


def markdown_sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) per heading of a Markdown document, in order."""
    sections: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if buffer and "".join(buffer).strip():
                sections.append((heading, "\n".join(buffer).strip()))
            heading = line.lstrip("#").strip().strip("*").strip()
            buffer = []
        else:
            buffer.append(line)
    if buffer and "".join(buffer).strip():
        sections.append((heading, "\n".join(buffer).strip()))
    if not sections:
        raise CorpusError("Markdown document has no content")
    return sections


def _chunk(text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [paragraph] if len(paragraph) <= PASSAGE_MAX_CHARS else _split_long(paragraph)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) > PASSAGE_TARGET_CHARS and current:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _split_long(paragraph: str) -> list[str]:
    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.;:])\s+", paragraph):
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > PASSAGE_TARGET_CHARS and current:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def split_passages(source_id: str, sections: list[tuple[str, str]]) -> tuple[Passage, ...]:
    passages: list[Passage] = []
    for heading, body in sections:
        for chunk in _chunk(body):
            passages.append(
                Passage(f"{source_id}#{len(passages)}", source_id, len(passages), heading, chunk)
            )
    return tuple(passages)


def _load_sections(path: Path, fmt: str) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    if fmt == "ecfr-xml":
        return ecfr_sections(text)
    if fmt == "markdown":
        return markdown_sections(text)
    raise CorpusError(f"unsupported corpus format {fmt!r} for {path}")


class CorpusIndex:
    """Documents keyed by source ID, citation URLs mapped to them, and a verifier."""

    def __init__(self, documents: dict[str, Document], not_retained: dict[str, str]) -> None:
        self.documents = documents
        self.not_retained = not_retained
        self._by_url = {
            url: doc.source_id for doc in documents.values() for url in doc.citation_urls
        }

    @classmethod
    def load(cls, root: Path) -> CorpusIndex:
        manifest_path = root / "corpus" / "SOURCES.json"
        try:
            manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CorpusError(f"cannot read {manifest_path}: {exc}") from exc
        documents: dict[str, Document] = {}
        for entry in manifest.get("sources", []):
            source_id = str(entry["source_id"])
            path = root / str(entry["local_copy"])
            if not path.is_file():
                raise CorpusError(f"{source_id}: local copy missing at {path}")
            sections = _load_sections(path, str(entry.get("format", "")))
            joined = "\n\n".join(f"{h}\n{b}" for h, b in sections)
            documents[source_id] = Document(
                source_id=source_id,
                label=str(entry.get("label", source_id)),
                citation_urls=tuple(str(u) for u in entry.get("citation_urls", [])),
                local_copy=str(entry["local_copy"]),
                sections=tuple(sections),
                passages=split_passages(source_id, sections),
                normalized=normalize_for_match(joined),
            )
        if not documents:
            raise CorpusError("corpus manifest lists no sources")
        not_retained = {
            str(item["citation_url"]): str(item.get("reason", ""))
            for item in manifest.get("not_retained", [])
        }
        return cls(documents, not_retained)

    def source_for_url(self, url: str) -> str | None:
        return self._by_url.get(url)

    def passages_for(self, source_ids: list[str] | tuple[str, ...]) -> list[Passage]:
        result: list[Passage] = []
        for source_id in source_ids:
            document = self.documents.get(source_id)
            if document:
                result.extend(document.passages)
        return result

    def passage(self, passage_id: str) -> Passage | None:
        source_id, _, index = passage_id.partition("#")
        document = self.documents.get(source_id)
        if document is None or not index.isdigit() or int(index) >= len(document.passages):
            return None
        return document.passages[int(index)]

    def verify_quote(self, source_id: str, quote: str) -> QuoteMatch | None:
        """Where ``quote`` occurs verbatim in the named document, or ``None``.

        Checked against the whole document, not the passage shown, so a
        faithful quote that straddles a passage boundary still verifies and a
        passage ID alone can never vouch for text.
        """
        document = self.documents.get(source_id)
        if document is None:
            return None
        needle = normalize_for_match(quote)
        if len(needle) < MIN_QUOTE_CHARS or needle not in document.normalized:
            return None
        for passage in document.passages:
            if needle in normalize_for_match(passage.text):
                return QuoteMatch(source_id, quote, passage.passage_id)
        return QuoteMatch(source_id, quote, None)

    def summary(self) -> dict[str, Any]:
        return {
            source_id: {
                "label": doc.label,
                "local_copy": doc.local_copy,
                "sections": len(doc.sections),
                "passages": len(doc.passages),
                "characters": len(doc.normalized),
            }
            for source_id, doc in sorted(self.documents.items())
        }
