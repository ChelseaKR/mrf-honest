"""Grounding evaluation for AI narration: how many claims survive the verifier.

Runs :func:`mrf_honest.ai.narrate.narrate` over committed assessment records
and counts, per record and overall, the claims generated, the claims shown
(every cited quote verified against the corpus), and the claims withheld. A
result file records provider, model, prompt version, UTC date, and the Git
commit, so a number quoted in a document is always traceable to one run.
Numbers are never written by hand.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mrf_honest.ai.corpus import CorpusIndex
from mrf_honest.ai.narrate import PROMPT_VERSION, NarrationError, narrate
from mrf_honest.ai.provider import Provider, ProviderError, provider_from_env


def load_records(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not records:
        raise ValueError(f"no records in {path}")
    return records[:limit] if limit else records


def score_record(
    record: Mapping[str, Any], *, corpus: CorpusIndex, provider: Provider
) -> dict[str, Any]:
    narration = narrate(record, corpus=corpus, provider=provider)
    shown = len(narration.claims)
    return {
        "subject": narration.subject,
        "grade": narration.grade,
        "finding_codes": list(narration.finding_codes),
        "offered_passages": len(narration.offered_passage_ids),
        "uncited_sources": list(narration.uncited_sources),
        "claims_generated": shown + narration.withheld_count,
        "claims_shown": shown,
        "claims_withheld": narration.withheld_count,
        "withheld_reasons": [list(w.reasons) for w in narration.withheld],
        "claims": [
            {
                "dimension": claim.dimension,
                "text": claim.text,
                "citations": [
                    {"passage_id": c.passage_id, "quote": c.quote} for c in claim.citations
                ],
            }
            for claim in narration.claims
        ],
        "input_tokens": narration.input_tokens,
        "output_tokens": narration.output_tokens,
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    generated = sum(int(r["claims_generated"]) for r in rows)
    shown = sum(int(r["claims_shown"]) for r in rows)
    return {
        "records": len(rows),
        "claims_generated": generated,
        "claims_shown": shown,
        "claims_withheld": generated - shown,
        "fraction_claims_with_verified_citations": round(shown / generated, 4)
        if generated
        else None,
        "records_with_no_withheld_claims": (
            round(sum(1 for r in rows if int(r["claims_withheld"]) == 0) / len(rows), 4)
            if rows
            else None
        ),
        "mean_claims_shown_per_record": round(shown / len(rows), 2) if rows else None,
    }


def run(
    records: Sequence[Mapping[str, Any]], *, corpus: CorpusIndex, provider: Provider
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for position, record in enumerate(records):
        try:
            rows.append(
                {"index": position, **score_record(record, corpus=corpus, provider=provider)}
            )
        except (NarrationError, ProviderError) as exc:
            errors.append({"index": str(position), "error": str(exc)})
    return {"summary": summarize(rows), "records": rows, "errors": errors}


def git_commit(root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        completed = subprocess.run(  # noqa: S603  # nosec B603
            [git, "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def metadata(provider: Provider, root: Path, records_path: Path) -> dict[str, Any]:
    return {
        "status": "recorded_live_run",
        "kind": "narration_grounding",
        "run_on": dt.datetime.now(dt.UTC).date().isoformat(),
        "provider": provider.name,
        "model": provider.model,
        "prompt_version": PROMPT_VERSION,
        "commit": git_commit(root),
        "records_file": str(records_path),
        "scoring": {
            "claims_shown": "every cited quote occurs verbatim in the named committed document",
            "claims_withheld": "at least one citation did not verify; the claim is not shown",
        },
    }


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI wrapper
    parser = argparse.ArgumentParser(description="Grounding evaluation for AI narration.")
    parser.add_argument("--records", type=Path, required=True, help="JSON Lines assessments")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        provider = provider_from_env()
    except ProviderError as exc:
        print(f"eval: cannot start: {exc}")
        return 2
    corpus = CorpusIndex.load(root)
    result = run(load_records(args.records, limit=args.limit), corpus=corpus, provider=provider)
    payload = {"run": metadata(provider, root, args.records), **result}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["summary"], indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
