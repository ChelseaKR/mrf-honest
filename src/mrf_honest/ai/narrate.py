"""Narrate one graded file in plain language, citing only the texts the findings cite.

The grade and every finding are inputs here, never outputs: this module reads
an assessment record that ``mrf_honest.scorecard`` and ``mrf_honest.cohort``
already produced and asks a model to explain it. The model is shown only
passages from the documents the record's own findings cite, it must quote
them verbatim for every claim, and a claim whose quote does not occur in the
named document is withheld and counted. The result is labeled AI-generated
and says what the verification does and does not establish.

A record that offers the model nothing to quote is not narrated at all. When
the findings cite no retained document, or there are no findings, every claim
the model could write would be withheld for lack of a citation, so the call is
refused before it is made, and the refusal is recorded in the narration's
provenance instead of a model name and a token count that bought nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from mrf_honest.ai.corpus import CorpusIndex, Passage
from mrf_honest.ai.provider import Provider
from mrf_honest.ai.retrieval import rank
from mrf_honest.cohort import grade_assessment
from mrf_honest.inspect import FINDING_CATALOG
from mrf_honest.inspect_csv import CSV_FINDING_CATALOG
from mrf_honest.scorecard import RETRIEVAL_FINDING_CATALOG

PROMPT_VERSION = "narrate-v1"
MAX_OUTPUT_TOKENS = 4000
PASSAGES_PER_FINDING = 3
MAX_PASSAGES = 16
LANGUAGES = ("en", "es")
DIMENSIONS = ("retrievability", "conformance", "completeness", "interpretability", "freshness")
LABEL = {
    "en": (
        "AI-generated narration of a deterministic grade. The grade and findings "
        "were computed by mrf-honest without a model; the model only explains them. "
        "Every statement shown quotes source text that was checked against the "
        "committed copy of that document; the check proves the passage exists and "
        "says those words, not that the statement is a correct reading of the "
        "regulation. This is not legal advice or a compliance determination."
    ),
    "es": (
        "Narración generada por IA de una calificación determinista. La calificación "
        "y los hallazgos los calculó mrf-honest sin ningún modelo; el modelo solo los "
        "explica. Cada enunciado mostrado cita un texto fuente verificado contra la "
        "copia publicada de ese documento; la verificación prueba que el pasaje "
        "existe y dice esas palabras, no que el enunciado sea una lectura correcta "
        "del reglamento. No es asesoría legal ni una determinación de cumplimiento."
    ),
}


class NarrationError(ValueError):
    """The record or the model output could not be used."""


@dataclass(frozen=True)
class Citation:
    passage_id: str
    source_id: str | None
    source_label: str | None
    quote: str
    verified: bool
    reason: str | None


@dataclass(frozen=True)
class Claim:
    text: str
    dimension: str | None
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class Withheld:
    text: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Narration:
    language: str
    subject: str
    grade: str
    grade_reason: str
    finding_codes: tuple[str, ...]
    claims: tuple[Claim, ...]
    withheld: tuple[Withheld, ...]
    offered_passage_ids: tuple[str, ...]
    uncited_sources: tuple[str, ...]
    label: str
    provider: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    #: Why the model was not called, or ``None`` when it was. Provenance, not prose: a
    #: narration with a refusal has no claims, no withheld claims, and zero tokens.
    refusal: str | None = None

    @property
    def withheld_count(self) -> int:
        return len(self.withheld)

    @property
    def model_called(self) -> bool:
        return self.refusal is None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["withheld_count"] = self.withheld_count
        payload["model_called"] = self.model_called
        return payload


_SYSTEM_PROMPT = """\
You explain, in plain language, why a hospital price-transparency file received
the grade it did. A deterministic program already graded the file and listed
its findings; you do not re-grade, soften, or extend that result. Your only job
is to say what each finding means for a reader, using the source passages
provided.

Hard rules:
1. Write only claims you can support with the provided passages. Each claim
   must cite one or more passages by passage_id, and for each citation copy a
   verbatim quote of at least eight consecutive words from that exact passage.
   Do not alter, shorten, or paraphrase inside a quote. A quote that is not an
   exact substring of the cited passage causes the whole claim to be withheld.
2. Do not cite a passage that was not provided. Do not invent section numbers,
   deadlines, penalties, or requirements. If a finding's sources were not
   provided, say nothing about that finding.
3. Describe; do not judge the hospital. Say what the regulation or data
   dictionary requires and what the file did or did not contain, as the
   findings state. Do not say the hospital is compliant or noncompliant, and
   do not predict enforcement.
4. Plain language: one requirement or number per sentence; define a term the
   first time it appears; keep sentences short. Set "dimension" on each claim
   to the dimension it explains, or "overall" for the grade as a whole.
5. Write between three and eight claims in the requested language. Quotes stay
   in the language of the source (English).
"""


def _finding_rows(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    scorecard = record.get("scorecard")
    rows: list[dict[str, Any]] = []
    if not isinstance(scorecard, Mapping):
        return rows
    for dimension in DIMENSIONS:
        block = scorecard.get(dimension)
        if not isinstance(block, Mapping):
            continue
        for finding in block.get("findings", []):
            if isinstance(finding, Mapping):
                rows.append({**finding, "dimension": dimension})
    return rows


def _dimension_lines(record: Mapping[str, Any]) -> str:
    scorecard = record.get("scorecard", {})
    lines = []
    for dimension in DIMENSIONS:
        block = scorecard.get(dimension, {}) if isinstance(scorecard, Mapping) else {}
        status = block.get("status", "unknown") if isinstance(block, Mapping) else "unknown"
        note = block.get("note") if isinstance(block, Mapping) else None
        lines.append(f"- {dimension}: {status}" + (f" ({note})" if note else ""))
    return "\n".join(lines)


def catalog_description(code: str) -> str:
    """The catalog's own description of a finding code, or an empty string."""
    for catalog in (FINDING_CATALOG, CSV_FINDING_CATALOG, RETRIEVAL_FINDING_CATALOG):
        definition = catalog.get(code)
        if definition is not None:
            return definition.description
    return ""


def grounding_passages(
    findings: Sequence[Mapping[str, Any]], corpus: CorpusIndex
) -> tuple[list[Passage], list[str]]:
    """Passages from the documents each finding cites, interleaved across findings.

    Returns the passages and the citation URLs that could not be resolved to a
    retained document (a claim about those findings cannot be verified).
    """
    per_finding: list[list[Passage]] = []
    unresolved: list[str] = []
    for finding in findings:
        sources: list[str] = []
        for url in finding.get("citations", []):
            source_id = corpus.source_for_url(str(url))
            if source_id is None:
                if str(url) not in unresolved:
                    unresolved.append(str(url))
            elif source_id not in sources:
                sources.append(source_id)
        query = " ".join(
            [
                str(finding.get("code", "")).replace("_", " "),
                str(finding.get("message", "")),
                catalog_description(str(finding.get("code", ""))),
            ]
        )
        ranked = rank(query, corpus.passages_for(sources), PASSAGES_PER_FINDING)
        per_finding.append([r.passage for r in ranked])
    chosen: dict[str, Passage] = {}
    depth = max((len(lst) for lst in per_finding), default=0)
    for position in range(depth):
        for lst in per_finding:
            if position < len(lst):
                chosen.setdefault(lst[position].passage_id, lst[position])
    return list(chosen.values())[:MAX_PASSAGES], unresolved


def narration_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "dimension": {"type": "string"},
                        "citations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "passage_id": {"type": "string"},
                                    "quote": {"type": "string"},
                                },
                                "required": ["passage_id", "quote"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["text", "dimension", "citations"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["claims"],
        "additionalProperties": False,
    }


def _subject_name(record: Mapping[str, Any]) -> str:
    subject = record.get("subject", {})
    publisher = subject.get("publisher", {}) if isinstance(subject, Mapping) else {}
    name = publisher.get("name") if isinstance(publisher, Mapping) else None
    return str(
        name or subject.get("location_id", "the file")
        if isinstance(subject, Mapping)
        else "the file"
    )


def _user_prompt(
    record: Mapping[str, Any],
    grade: str,
    reason: str,
    findings: Sequence[Mapping[str, Any]],
    passages: Sequence[Passage],
    corpus: CorpusIndex,
    language: str,
) -> str:
    language_name = "Spanish" if language == "es" else "English"
    finding_lines = (
        "\n".join(
            f"- [{f.get('dimension')}] {f.get('code')} ({f.get('severity')}, "
            f"{f.get('occurrences', 1)} occurrence(s)): {f.get('message')}"
            for f in findings
        )
        or "- none"
    )
    passage_lines = "\n".join(
        f'<passage id="{p.passage_id}" source="{corpus.documents[p.source_id].label}" '
        f'heading="{p.heading}">\n{p.text}\n</passage>'
        for p in passages
    )
    return "\n\n".join(
        [
            f"Write the claims in {language_name}.",
            f"File: {_subject_name(record)} (assessed as of {record.get('as_of')}).",
            f"Grade: {grade}. Reason recorded by the grader: {reason}",
            f"Dimensions:\n{_dimension_lines(record)}",
            f"Findings (deterministic; do not re-evaluate):\n{finding_lines}",
            f"Source passages (cite by passage_id; quote verbatim):\n{passage_lines}",
        ]
    )


def _verify(raw: Any, offered: Mapping[str, Passage], corpus: CorpusIndex) -> Claim | Withheld:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("text"), str):
        return Withheld("", ("malformed claim",))
    text = raw["text"].strip()
    if not text:
        return Withheld("", ("empty claim",))
    raw_citations = raw.get("citations")
    if not isinstance(raw_citations, list) or not raw_citations:
        return Withheld(text, ("no citation",))
    citations: list[Citation] = []
    reasons: list[str] = []
    for item in raw_citations:
        passage_id = str(item.get("passage_id", "")) if isinstance(item, Mapping) else ""
        quote = str(item.get("quote", "")) if isinstance(item, Mapping) else ""
        passage = offered.get(passage_id)
        if passage is None:
            citation = Citation(passage_id, None, None, quote, False, "passage was not offered")
        else:
            label = corpus.documents[passage.source_id].label
            match = corpus.verify_quote(passage.source_id, quote)
            citation = Citation(
                passage_id,
                passage.source_id,
                label,
                quote,
                match is not None,
                None if match else "quote does not occur in the source text",
            )
        citations.append(citation)
        if not citation.verified:
            reasons.append(f"{passage_id}: {citation.reason} (quote: {quote[:120]!r})")
    if reasons:
        return Withheld(text, tuple(reasons))
    dimension = raw.get("dimension")
    return Claim(
        text,
        dimension if isinstance(dimension, str) and dimension in DIMENSIONS else None,
        tuple(citations),
    )


REFUSAL_NO_FINDINGS = (
    "not narrated: the record carries no findings, so no source passage could be offered "
    "and the model was not called"
)
REFUSAL_NO_RETAINED_SOURCE = (
    "not narrated: none of the documents the findings cite is retained in the corpus, so no "
    "source passage could be offered and the model was not called"
)


def refusal_reason(
    findings: Sequence[Mapping[str, Any]], passages: Sequence[Passage]
) -> str | None:
    """Why a model call would produce nothing showable, or ``None`` if it might.

    Every claim must quote an offered passage. With no passage to offer, every claim is
    withheld for lack of a citation, so calling the model spends tokens to say nothing.
    """
    if passages:
        return None
    return REFUSAL_NO_FINDINGS if not findings else REFUSAL_NO_RETAINED_SOURCE


def narrate(
    record: Mapping[str, Any],
    *,
    corpus: CorpusIndex,
    provider: Provider,
    language: str = "en",
) -> Narration:
    """Explain one assessment record; the grade comes from ``grade_assessment``.

    Returns a narration with ``refusal`` set, no claims, and no model call when the record
    offers nothing the model could quote (:func:`refusal_reason`).
    """
    if language not in LANGUAGES:
        raise NarrationError(f"language must be one of {', '.join(LANGUAGES)}")
    if "scorecard" not in record or "subject" not in record:
        raise NarrationError("record is not an assessment: missing scorecard or subject")
    graded = grade_assessment(record)
    findings = _finding_rows(record)
    passages, unresolved = grounding_passages(findings, corpus)
    offered = {p.passage_id: p for p in passages}
    refusal = refusal_reason(findings, passages)
    if refusal is not None:
        return Narration(
            language=language,
            subject=_subject_name(record),
            grade=graded.grade,
            grade_reason=graded.reason,
            finding_codes=tuple(str(f.get("code")) for f in findings),
            claims=(),
            withheld=(),
            offered_passage_ids=(),
            uncited_sources=tuple(unresolved),
            label=LABEL[language],
            provider=provider.name,
            model=provider.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=0,
            output_tokens=0,
            refusal=refusal,
        )
    completion = provider.complete_json(
        system=_SYSTEM_PROMPT,
        user=_user_prompt(
            record, graded.grade, graded.reason, findings, passages, corpus, language
        ),
        schema=narration_schema(),
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    try:
        parsed = json.loads(completion.text)
    except ValueError as exc:
        raise NarrationError("the model did not return JSON") from exc
    raw_claims = parsed.get("claims") if isinstance(parsed, dict) else None
    if not isinstance(raw_claims, list):
        raise NarrationError("the model did not return a claims list")
    claims: list[Claim] = []
    withheld: list[Withheld] = []
    for raw in raw_claims:
        outcome = _verify(raw, offered, corpus)
        if isinstance(outcome, Claim):
            claims.append(outcome)
        else:
            withheld.append(outcome)
    return Narration(
        language=language,
        subject=_subject_name(record),
        grade=graded.grade,
        grade_reason=graded.reason,
        finding_codes=tuple(str(f.get("code")) for f in findings),
        claims=tuple(claims),
        withheld=tuple(withheld),
        offered_passage_ids=tuple(offered),
        uncited_sources=tuple(unresolved),
        label=LABEL[language],
        provider=completion.provider,
        model=completion.model,
        prompt_version=PROMPT_VERSION,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
    )
