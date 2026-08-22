# Narration grounding evaluation (ADR 0006)

`python -m mrf_honest.ai.eval --records <assessments.jsonl> --output evals/ai/results/<date>-<cohort>-<provider>-<model>.json`
runs `mrf-honest narrate` over committed assessment records and counts, per
record and overall, the claims the model generated, the claims shown (every
cited quote occurs verbatim in the named committed document under `corpus/`),
and the claims withheld. It does not measure whether a shown claim is a
correct reading of the passage it quotes, and no gold explanations exist; a
verified citation proves the passage exists and says those words.

Each result records provider, model, prompt version, UTC date, the Git commit,
and the records file, and `tests/test_ai_narration.py` refuses a result
without that provenance. Numbers are never written by hand; an `ai` extra and
`MRF_AI_PROVIDER` / `MRF_AI_MODEL` (Anthropic API or Amazon Bedrock through the
public SDK) are required to run it.
