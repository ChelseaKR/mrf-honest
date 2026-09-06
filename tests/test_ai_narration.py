"""AI narration outside the graded path: corpus verifier, narration, eval, CLI."""

from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from mrf_honest.ai import corpus as corpus_module
from mrf_honest.ai import eval as eval_module
from mrf_honest.ai.corpus import (
    MIN_QUOTE_CHARS,
    CorpusError,
    CorpusIndex,
    ecfr_sections,
    markdown_sections,
    normalize_for_match,
    split_passages,
)
from mrf_honest.ai.narrate import (
    LABEL,
    REFUSAL_NO_FINDINGS,
    REFUSAL_NO_RETAINED_SOURCE,
    NarrationError,
    catalog_description,
    grounding_passages,
    narrate,
    narration_schema,
    refusal_reason,
)
from mrf_honest.ai.provider import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_BEDROCK_MODEL,
    ProviderError,
    ScriptedProvider,
    SDKProvider,
    Settings,
    provider_from_env,
    provider_from_settings,
)
from mrf_honest.ai.retrieval import rank, tokenize
from mrf_honest.cli import main
from mrf_honest.cohort import grade_assessment
from mrf_honest.inspect import CFR_180_50, CMS_JSON_DICTIONARY, CMS_V3_SCHEMA, FINDING_CATALOG
from mrf_honest.inspect_csv import CMS_CSV_DICTIONARY, CSV_FINDING_CATALOG
from mrf_honest.scorecard import CMS_HPT_POLICY_FAQ, RETRIEVAL_FINDING_CATALOG

ROOT = Path(__file__).resolve().parents[1]
CORPUS = CorpusIndex.load(ROOT)
RECORDS = [
    json.loads(line)
    for line in (ROOT / "data" / "cohorts" / "2026-08-19.assessments.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]


def _real_quote(passage_id: str, words: int = 12) -> str:
    passage = CORPUS.passage(passage_id)
    assert passage is not None
    return " ".join(passage.text.split()[:words])


# --- corpus -----------------------------------------------------------------


def test_every_catalog_citation_resolves_to_a_retained_document_or_is_listed() -> None:
    urls = {
        url
        for catalog in (FINDING_CATALOG, CSV_FINDING_CATALOG, RETRIEVAL_FINDING_CATALOG)
        for definition in catalog.values()
        for url in definition.citations
    }
    for url in urls:
        assert CORPUS.source_for_url(url) is not None or url in CORPUS.not_retained, url
    assert CORPUS.source_for_url(CFR_180_50) == "cfr-45-part-180"
    assert CORPUS.source_for_url(CMS_JSON_DICTIONARY) == "cms-json-data-dictionary"
    assert CORPUS.source_for_url(CMS_V3_SCHEMA) == "cms-json-data-dictionary"
    assert CORPUS.source_for_url(CMS_CSV_DICTIONARY) == "cms-csv-data-dictionary"
    assert CMS_HPT_POLICY_FAQ in CORPUS.not_retained


def test_committed_corpus_matches_its_manifest_hashes() -> None:
    import hashlib

    manifest = json.loads((ROOT / "corpus" / "SOURCES.json").read_text(encoding="utf-8"))
    for entry in manifest["sources"]:
        digest = hashlib.sha256((ROOT / entry["local_copy"]).read_bytes()).hexdigest()
        assert digest == entry["sha256"], entry["source_id"]
    summary = CORPUS.summary()
    assert summary["cfr-45-part-180"]["sections"] == 11
    assert all(item["passages"] > 0 for item in summary.values())


def test_ecfr_and_markdown_section_parsers() -> None:
    xml = (
        "<DIV5><DIV8 N='180.50'><HEAD>§ 180.50 Requirements.</HEAD><P>(a) General rule. "
        "Each hospital must make public its standard charges.</P><P></P></DIV8>"
        "<DIV8 N='180.60'><HEAD>§ 180.60 Other.</HEAD></DIV8></DIV5>"
    )
    sections = ecfr_sections(xml)
    assert sections[0][0] == "§ 180.50 Requirements."
    assert "standard charges" in sections[0][1]
    assert sections[1] == ("§ 180.60 Other.", "")
    with pytest.raises(CorpusError, match="no DIV8"):
        ecfr_sections("<DIV5></DIV5>")
    md = "intro\n\n# **Title**\n\nbody one\n\n## Second\n\nbody two\n"
    assert markdown_sections(md) == [
        ("", "intro"),
        ("Title", "body one"),
        ("Second", "body two"),
    ]
    with pytest.raises(CorpusError, match="no content"):
        markdown_sections("\n\n")


def test_passage_splitting_bounds_size() -> None:
    long = "Sentence one is here. " * 200
    passages = split_passages("doc", [("H", long), ("I", "short")])
    assert all(len(p.text) <= corpus_module.PASSAGE_MAX_CHARS for p in passages)
    assert [p.index for p in passages] == list(range(len(passages)))
    assert passages[-1].heading == "I" and passages[-1].text == "short"


def test_verify_quote_is_verbatim_but_typography_tolerant() -> None:
    passage = CORPUS.documents["cfr-45-part-180"].passages[5]
    quote = " ".join(passage.text.split()[:10])
    assert CORPUS.verify_quote("cfr-45-part-180", quote) is not None
    folded = quote.upper().replace("'", "\u2019").replace("-", "\u2013")
    assert CORPUS.verify_quote("cfr-45-part-180", folded) is not None
    assert CORPUS.verify_quote("cfr-45-part-180", quote + " and also some invented words") is None
    assert CORPUS.verify_quote("cfr-45-part-180", "too short") is None
    assert len(normalize_for_match("too short")) < MIN_QUOTE_CHARS
    assert CORPUS.verify_quote("missing", quote) is None
    assert CORPUS.passage("cfr-45-part-180#9999") is None
    assert CORPUS.passage("nope#0") is None and CORPUS.passage("cfr-45-part-180#x") is None


def test_corpus_load_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="cannot read"):
        CorpusIndex.load(tmp_path)
    (tmp_path / "corpus").mkdir()
    manifest = tmp_path / "corpus" / "SOURCES.json"
    manifest.write_text(json.dumps({"sources": []}), encoding="utf-8")
    with pytest.raises(CorpusError, match="no sources"):
        CorpusIndex.load(tmp_path)
    manifest.write_text(
        json.dumps(
            {"sources": [{"source_id": "x", "local_copy": "corpus/x.md", "format": "markdown"}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="missing"):
        CorpusIndex.load(tmp_path)
    (tmp_path / "corpus" / "x.md").write_text("# T\n\nbody", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {"sources": [{"source_id": "x", "local_copy": "corpus/x.md", "format": "weird"}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="unsupported"):
        CorpusIndex.load(tmp_path)


def test_retrieval_ranks_relevant_passages() -> None:
    passages = CORPUS.passages_for(["cfr-45-part-180"])
    ranked = rank("machine-readable file standard charges", passages, 3)
    assert ranked and "standard charge" in ranked[0].passage.text.lower()
    assert rank("", passages, 3) == [] and rank("zzzz", passages, 3) == []
    assert rank("file", passages, 0) == []
    assert tokenize("The § 180.50 file is here") == ["§", "180.50", "file", "here"]


# --- narration --------------------------------------------------------------


def _claims_reply(passages: list[str], *, bad: bool = False) -> str:
    claims: list[dict[str, Any]] = [
        {
            "text": "Supported claim.",
            "dimension": "interpretability",
            "citations": [{"passage_id": passages[0], "quote": _real_quote(passages[0])}],
        }
    ]
    if bad:
        claims += [
            {
                "text": "Altered quote.",
                "dimension": "conformance",
                "citations": [
                    {"passage_id": passages[0], "quote": "words that are nowhere in the text ok"}
                ],
            },
            {
                "text": "Unoffered passage.",
                "dimension": "overall",
                "citations": [
                    {"passage_id": "cfr-45-part-180#0", "quote": _real_quote("cfr-45-part-180#0")}
                ],
            },
            {"text": "No citation.", "dimension": "freshness", "citations": []},
            {"text": "", "dimension": "freshness", "citations": []},
            "junk",
        ]
    return json.dumps({"claims": claims})


def test_grounding_is_scoped_to_the_findings_sources() -> None:
    record = RECORDS[0]
    findings = [
        {**f, "dimension": d}
        for d, block in record["scorecard"].items()
        for f in block.get("findings", [])
    ]
    passages, unresolved = grounding_passages(findings, CORPUS)
    allowed = {CORPUS.source_for_url(u) for f in findings for u in f["citations"]}
    assert passages and {p.source_id for p in passages} <= allowed
    assert unresolved == []
    passages2, unresolved2 = grounding_passages(
        [
            {
                "code": "MRF_AUTOMATION_BARRIER_OBSERVED",
                "message": "x",
                "citations": [CMS_HPT_POLICY_FAQ],
            }
        ],
        CORPUS,
    )
    assert passages2 == [] and unresolved2 == [CMS_HPT_POLICY_FAQ]
    assert catalog_description("JSON_UTF8_BOM_PRESENT")
    assert catalog_description("CMS_CSV_UTF8_BOM_PRESENT")
    assert catalog_description("MRF_DIRECT_DOWNLOAD_FAILED")
    assert catalog_description("NOT_A_CODE") == ""


def test_narration_keeps_verified_claims_and_withholds_the_rest() -> None:
    record = RECORDS[0]
    findings = [
        {**f, "dimension": d}
        for d, block in record["scorecard"].items()
        for f in block.get("findings", [])
    ]
    offered = [p.passage_id for (p,) in ((p,) for p in grounding_passages(findings, CORPUS)[0])]
    provider = ScriptedProvider([_claims_reply(offered, bad=True)])
    narration = narrate(record, corpus=CORPUS, provider=provider)
    assert narration.grade == grade_assessment(record).grade
    assert [c.text for c in narration.claims] == ["Supported claim."]
    assert narration.claims[0].dimension == "interpretability"
    assert narration.claims[0].citations[0].verified
    assert narration.withheld_count == 5
    reasons = {w.text: w.reasons for w in narration.withheld}
    assert any("does not occur" in r for r in reasons["Altered quote."])
    assert any("not offered" in r for r in reasons["Unoffered passage."])
    assert reasons["No citation."] == ("no citation",)
    assert narration.label == LABEL["en"]
    assert narration.offered_passage_ids == tuple(offered)
    assert narration.to_dict()["withheld_count"] == 5
    call = provider.calls[0]
    assert "Write the claims in English." in call.user
    assert f"Grade: {narration.grade}." in call.user
    assert call.schema == narration_schema()


def test_narration_in_spanish_and_error_paths() -> None:
    record = RECORDS[0]
    spanish = narrate(
        record, corpus=CORPUS, provider=ScriptedProvider(['{"claims": []}']), language="es"
    )
    assert spanish.label == LABEL["es"] and spanish.claims == ()
    with pytest.raises(NarrationError, match="language"):
        narrate(record, corpus=CORPUS, provider=ScriptedProvider([]), language="fr")
    with pytest.raises(NarrationError, match="not an assessment"):
        narrate({"subject": {}}, corpus=CORPUS, provider=ScriptedProvider([]))
    with pytest.raises(NarrationError, match="did not return JSON"):
        narrate(record, corpus=CORPUS, provider=ScriptedProvider(["?"]))
    with pytest.raises(NarrationError, match="claims list"):
        narrate(record, corpus=CORPUS, provider=ScriptedProvider(['{"claims": 1}']))


def _without_findings(record: dict[str, Any]) -> dict[str, Any]:
    """Cohort record 0 with every finding removed: the shape issue #26 was observed on."""
    stripped = copy.deepcopy(record)
    for block in stripped["scorecard"].values():
        if isinstance(block, dict):
            block["findings"] = []
            if block.get("status") == "FINDINGS":
                block["status"] = "OBSERVED"
    return stripped


def test_a_record_that_offers_no_passage_is_refused_before_the_model_is_called() -> None:
    """Issue #26: the model was called on a record with no findings, wrote claims, and every
    one was withheld for lack of a citation. The promise held, at 962 input tokens per
    language, to say nothing. A provider with no scripted reply proves the call is never made.
    """
    stripped = _without_findings(RECORDS[0])
    assert grade_assessment(stripped).grade == "A"
    for language in ("en", "es"):
        provider = ScriptedProvider([])
        narration = narrate(stripped, corpus=CORPUS, provider=provider, language=language)
        assert provider.calls == []
        assert narration.refusal == REFUSAL_NO_FINDINGS
        assert narration.model_called is False
        assert narration.claims == () and narration.withheld == ()
        assert narration.offered_passage_ids == () and narration.uncited_sources == ()
        assert narration.finding_codes == ()
        assert (narration.input_tokens, narration.output_tokens) == (0, 0)
        # Provenance still names the provider and model the layer would have used, and the
        # grade it was asked to explain, so a refusal is traceable the way a narration is.
        assert (narration.provider, narration.model) == ("scripted", "scripted-model")
        assert narration.grade == "A" and narration.label == LABEL[language]
        payload = narration.to_dict()
        assert payload["refusal"] == REFUSAL_NO_FINDINGS
        assert payload["model_called"] is False and payload["withheld_count"] == 0

    # Findings whose only cited document is not retained offer nothing either; the refusal
    # names that reason and the unresolved source stays listed.
    uncited = _without_findings(RECORDS[0])
    uncited["scorecard"]["retrievability"]["status"] = "FINDINGS"
    uncited["scorecard"]["retrievability"]["findings"] = [
        {
            "code": "MRF_AUTOMATION_BARRIER_OBSERVED",
            "message": "x",
            "severity": "INFO",
            "citations": [CMS_HPT_POLICY_FAQ],
            "occurrences": 1,
        }
    ]
    provider = ScriptedProvider([])
    narration = narrate(uncited, corpus=CORPUS, provider=provider)
    assert provider.calls == []
    assert narration.refusal == REFUSAL_NO_RETAINED_SOURCE
    assert narration.uncited_sources == (CMS_HPT_POLICY_FAQ,)
    assert narration.finding_codes == ("MRF_AUTOMATION_BARRIER_OBSERVED",)

    # A record with a passage to offer is not refused; the existing tests cover that call.
    assert refusal_reason([], []) == REFUSAL_NO_FINDINGS
    assert refusal_reason([{"code": "x"}], []) == REFUSAL_NO_RETAINED_SOURCE
    passage = CORPUS.passages_for(["cfr-45-part-180"])[0]
    assert refusal_reason([{"code": "x"}], [passage]) is None
    assert (
        narrate(RECORDS[0], corpus=CORPUS, provider=ScriptedProvider(['{"claims": []}'])).refusal
        is None
    )


# --- provider ----------------------------------------------------------------


class _Block:
    def __init__(self, type_: str, text: str = "") -> None:
        self.type = type_
        self.text = text


class _Response:
    def __init__(self, blocks: list[_Block], stop_reason: str) -> None:
        self.content = blocks
        self.stop_reason = stop_reason
        self.model = "served"
        self.usage = types.SimpleNamespace(input_tokens=3, output_tokens=2)


class _Client:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_sdk_provider_translates_outcomes() -> None:
    client = _Client(_Response([_Block("text", "{}")], "end_turn"))
    provider = SDKProvider(client, model="m", name="anthropic")
    completion = provider.complete_json(system="s", user="u", schema={}, max_tokens=5)
    assert (completion.text, completion.model, completion.input_tokens) == ("{}", "served", 3)
    assert client.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert provider.name == "anthropic" and provider.model == "m"
    for response, message in (
        (_Response([_Block("text", "{}")], "refusal"), "declined"),
        (_Response([_Block("text", "{")], "max_tokens"), "truncated"),
        (_Response([_Block("text", " ")], "end_turn"), "no text"),
    ):
        with pytest.raises(ProviderError, match=message):
            SDKProvider(_Client(response), model="m", name="x").complete_json(
                system="s", user="u", schema={}, max_tokens=5
            )
    import anthropic
    import httpx2 as httpx  # the SDK's own HTTP client package

    request = httpx.Request("POST", "https://example.test")
    status = anthropic.APIStatusError(
        "boom", response=httpx.Response(500, request=request), body=None
    )
    with pytest.raises(ProviderError, match="status 500"):
        SDKProvider(_Client(status), model="m", name="x").complete_json(
            system="s", user="u", schema={}, max_tokens=5
        )
    with pytest.raises(ProviderError, match="unreachable"):
        SDKProvider(
            _Client(anthropic.APIConnectionError(request=request)), model="m", name="x"
        ).complete_json(system="s", user="u", schema={}, max_tokens=5)


def test_settings_and_provider_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.from_environ({}) == Settings("anthropic", "claude-sonnet-5", None)
    bedrock = Settings.from_environ({"MRF_AI_PROVIDER": "bedrock", "AWS_REGION": "us-east-1"})
    assert bedrock.region == "us-east-1" and bedrock.model == "global.anthropic.claude-sonnet-4-6"
    with pytest.raises(ProviderError, match="MRF_AI_PROVIDER"):
        Settings.from_environ({"MRF_AI_PROVIDER": "openai"})

    class _Error(Exception):
        pass

    built: list[str] = []
    fake = types.SimpleNamespace(
        Anthropic=lambda **kw: built.append("anthropic") or object(),
        AnthropicBedrock=lambda **kw: built.append("bedrock") or object(),
        AnthropicError=_Error,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert provider_from_env({}).name == "anthropic"
    assert provider_from_settings(Settings("bedrock", "m", "us-west-2")).name == "bedrock"
    assert built == ["anthropic", "bedrock"]

    def failing(**kw: Any) -> object:
        raise _Error("no credential")

    fake.Anthropic = failing
    with pytest.raises(ProviderError, match="could not configure"):
        provider_from_env({})
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ProviderError, match="could not be imported"):
        provider_from_settings(Settings("anthropic", "m", None))
    scripted = ScriptedProvider([])
    with pytest.raises(ProviderError, match="no response left"):
        scripted.complete_json(system="s", user="u", schema={}, max_tokens=1)


def test_the_bedrock_default_is_a_model_this_project_has_actually_invoked() -> None:
    """A default nobody can call is a default that has never been tested.

    ``DEFAULT_BEDROCK_MODEL`` was ``global.anthropic.claude-sonnet-5``, which the AWS
    account these evaluations run under cannot invoke: Bedrock answers
    ``AccessDeniedException`` for it while the entitlement API reports it authorized, so
    the failure only appears on a real call. Every recorded run in ``evals/ai/results/``
    is on ``global.anthropic.claude-sonnet-4-6``, which is the evidence that the default
    is checked against here rather than a second hand-typed copy of the same string. The
    Anthropic default is deliberately *not* pinned to it: a deployer with ordinary API
    access should get the current model, and the two constants differ on purpose.
    """

    recorded = {
        json.loads(path.read_text(encoding="utf-8"))["run"]["model"]
        for path in sorted((ROOT / "evals" / "ai" / "results").glob("*.json"))
    }
    assert recorded, "no recorded evaluation run to check the Bedrock default against"
    assert DEFAULT_BEDROCK_MODEL in recorded, (
        f"DEFAULT_BEDROCK_MODEL is {DEFAULT_BEDROCK_MODEL!r}, which no recorded run in "
        f"evals/ai/results/ used; the recorded models are {sorted(recorded)}. Raise this "
        "constant only alongside a recorded run proving the account can invoke the model."
    )
    assert DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-5"
    assert DEFAULT_ANTHROPIC_MODEL != DEFAULT_BEDROCK_MODEL


# --- eval and CLI -----------------------------------------------------------


def test_eval_scores_records_and_records_provenance(tmp_path: Path) -> None:
    records = RECORDS[:2]
    findings = [
        {**f, "dimension": d}
        for d, block in records[0]["scorecard"].items()
        for f in block.get("findings", [])
    ]
    offered = [p.passage_id for p in grounding_passages(findings, CORPUS)[0]]
    provider = ScriptedProvider([_claims_reply(offered, bad=True), "not json"])
    result = eval_module.run(
        [*records, _without_findings(records[0])], corpus=CORPUS, provider=provider
    )
    assert result["summary"]["records"] == 2
    assert result["summary"]["claims_generated"] == 6
    assert result["summary"]["claims_shown"] == 1
    assert result["summary"]["fraction_claims_with_verified_citations"] == round(1 / 6, 4)
    assert result["summary"]["records_refused_before_model_call"] == 1
    assert result["errors"] == [{"index": "1", "error": "the model did not return JSON"}]
    refused = result["records"][1]
    assert refused["index"] == 2 and refused["model_called"] is False
    assert refused["refusal"] == REFUSAL_NO_FINDINGS
    assert refused["claims_generated"] == 0 and refused["input_tokens"] == 0
    assert result["records"][0]["model_called"] is True
    assert result["records"][0]["refusal"] is None
    assert eval_module.summarize([])["records"] == 0
    assert eval_module.summarize([])["records_refused_before_model_call"] == 0
    meta = eval_module.metadata(
        provider, ROOT, ROOT / "data" / "cohorts" / "2026-08-19.assessments.jsonl"
    )
    assert meta["status"] == "recorded_live_run" and len(meta["commit"]) == 40
    assert eval_module.git_commit(tmp_path) == "unknown"
    with pytest.raises(ValueError, match="no records"):
        eval_module.load_records(tmp_path / "empty.jsonl") if (
            (tmp_path / "empty.jsonl").write_text("\n", encoding="utf-8") or True
        ) else None
    assert (
        len(
            eval_module.load_records(
                ROOT / "data" / "cohorts" / "2026-08-19.assessments.jsonl", limit=3
            )
        )
        == 3
    )


def test_committed_results_carry_provenance() -> None:
    results = sorted((ROOT / "evals" / "ai" / "results").glob("*.json"))
    assert results
    for path in results:
        payload = json.loads(path.read_text(encoding="utf-8"))
        run = payload["run"]
        assert run["status"] in {"recorded_live_run", "not_run"}
        if run["status"] == "recorded_live_run":
            assert run["provider"] in {"anthropic", "bedrock"} and len(run["commit"]) == 40
            assert payload["summary"]["records"] > 0


def test_narrate_cli_prints_claims_and_validates_index(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    findings = [
        {**f, "dimension": d}
        for d, block in RECORDS[0]["scorecard"].items()
        for f in block.get("findings", [])
    ]
    offered = [p.passage_id for p in grounding_passages(findings, CORPUS)[0]]
    replies = iter([_claims_reply(offered, bad=True), _claims_reply(offered)])
    monkeypatch.setattr(
        "mrf_honest.ai.provider.provider_from_env", lambda: ScriptedProvider([next(replies)])
    )
    records = ROOT / "data" / "cohorts" / "2026-08-19.assessments.jsonl"
    assert main(["narrate", "--assessments", str(records), "--root", str(ROOT)]) == 0
    out = capsys.readouterr().out
    assert ": grade " in out and "1. Supported claim." in out
    assert "5 statement(s) withheld" in out and "Prompt version: narrate-v1" in out
    assert main(["narrate", "--assessments", str(records), "--root", str(ROOT), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["withheld_count"] == 0 and payload["claims"][0]["text"] == "Supported claim."
    assert main(["narrate", "--assessments", str(records), "--index", "99"]) == 1
    assert "--index must be between" in capsys.readouterr().err
    stripped = tmp_path / "stripped.jsonl"
    stripped.write_text(json.dumps(_without_findings(RECORDS[0])) + "\n", encoding="utf-8")
    monkeypatch.setattr("mrf_honest.ai.provider.provider_from_env", lambda: ScriptedProvider([]))
    assert main(["narrate", "--assessments", str(stripped), "--root", str(ROOT)]) == 0
    out = capsys.readouterr().out
    assert REFUSAL_NO_FINDINGS in out and "(not called)" in out
    assert LABEL["en"] not in out, "an AI-generated label on text nobody generated"
    assert main(["narrate", "--assessments", str(stripped), "--root", str(ROOT), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model_called"] is False and payload["refusal"] == REFUSAL_NO_FINDINGS
