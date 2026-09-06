"""Behavioral tests for the publisher-side gate, its exit codes, and its two packagings.

The properties these hold on to, in order of how badly a regression would matter:

1. a file that could not be read exits 2 and never carries a letter;
2. the gate's letter is the *committed* presentation grade, under the same policy fingerprint
   the published site prints, not a second scale invented here;
3. a dimension that was not assessed is named in the report, because no ``--fail-on`` severity
   can see it and a reader must not have to infer it from the letter;
4. the shipped ``action.yml`` and ``.pre-commit-hooks.yaml`` invoke a command this CLI has.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest
import yaml

from mrf_honest import gate
from mrf_honest.cli import main
from mrf_honest.cohort import (
    CSV_GRADE_POLICY_VERSION,
    GRADE_POLICY_FINGERPRINT,
    GRADE_POLICY_VERSION,
    CohortError,
    grade_local_evidence,
)
from mrf_honest.inspect_csv import ATTESTATION_HEADER_TEXT

AS_OF = date(2026, 8, 19)
REPO_ROOT = Path(__file__).resolve().parents[1]

CSV_GENERAL_HEADER = (
    "hospital_name,last_updated_on,version,location_name,hospital_address,"
    f'license_number|CA,type_2_npi,"{ATTESTATION_HEADER_TEXT}",attester_name'
)
CSV_GENERAL_VALUES = (
    'Example Hospital,2026-05-01,3.0.0,Example Hospital,"1 Main St, Sacramento, CA 95814",'
    "030000123,1234567890,true,Jane Doe"
)
CSV_TALL_HEADER = (
    "description,code|1,code|1|type,modifiers,setting,drug_unit_of_measurement,"
    "drug_type_of_measurement,standard_charge|gross,standard_charge|discounted_cash,"
    "payer_name,plan_name,standard_charge|negotiated_dollar,"
    "standard_charge|negotiated_percentage,standard_charge|negotiated_algorithm,"
    "median_amount,10th_percentile,90th_percentile,count,standard_charge|methodology,"
    "standard_charge|min,standard_charge|max,additional_generic_notes"
)
CSV_TALL_ROW = (
    "MRI brain,70551,CPT,,outpatient,,,1200,900,Acme Health,PPO,800,,,,,,,fee schedule,700,950,"
)


def _payers() -> list[dict[str, object]]:
    allowed = {"standard_charge_dollar": 800}
    return [
        {
            "payer_name": "Acme",
            "plan_name": "PPO",
            "methodology": "fee schedule",
            **allowed,
        }
    ]


def _json_document(*, version: str = "3.0.0", updated: str = "2026-08-01") -> dict[str, object]:
    return {
        "hospital_name": "Example Hospital",
        "last_updated_on": updated,
        "version": version,
        "location_name": ["Example Main"],
        "hospital_address": ["1 Main St, Testville, CA 90000"],
        "type_2_npi": ["1234567890"],
        "license_information": {"state": "CA", "license_number": "A1"},
        "attestation": {
            "attestation": "CMS-required statement represented in the fixture",
            "confirm_attestation": True,
            "attester_name": "A. Executive",
        },
        "standard_charge_information": [
            {
                "description": "Example service",
                "code_information": [{"code": "12345", "type": "CPT"}],
                "standard_charges": [
                    {
                        "gross_charge": 120,
                        "discounted_cash": 90,
                        "minimum": 70,
                        "maximum": 100,
                        "setting": "outpatient",
                        "payers_information": _payers(),
                    }
                ],
            }
        ],
    }


def _write_json(tmp_path: Path, document: object, name: str = "prices.json") -> Path:
    target = tmp_path / name
    target.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    return target


def _write_csv(tmp_path: Path, *, version: str = "3.0.0", name: str = "prices.csv") -> Path:
    values = CSV_GENERAL_VALUES.replace(",3.0.0,", f",{version},")
    target = tmp_path / name
    target.write_text(
        "\n".join([CSV_GENERAL_HEADER, values, CSV_TALL_HEADER, CSV_TALL_ROW]) + "\n",
        encoding="utf-8",
    )
    return target


def _run(path: Path, **kwargs: str) -> gate.GateReport:
    options: dict[str, str] = {"profile": "auto", "min_grade": "none", "fail_on": "never"}
    options.update(kwargs)
    return gate.run_gate([path], as_of=AS_OF, **options)  # type: ignore[arg-type]


# --- profile detection ------------------------------------------------------------------------


def test_a_json_object_detects_as_the_json_profile(tmp_path: Path) -> None:
    assert gate.detect_profile(_write_json(tmp_path, _json_document())) == "json"


def test_a_leading_bom_does_not_hide_the_json_object(tmp_path: Path) -> None:
    target = tmp_path / "bom.json"
    target.write_bytes(b"\xef\xbb\xbf" + json.dumps(_json_document()).encode())
    assert gate.detect_profile(target) == "json"


def test_a_header_row_detects_as_the_csv_profile(tmp_path: Path) -> None:
    assert gate.detect_profile(_write_csv(tmp_path)) == "csv"


def test_bytes_that_are_neither_detect_as_neither(tmp_path: Path) -> None:
    target = tmp_path / "image.bin"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")
    assert gate.detect_profile(target) is None


def test_an_empty_file_detects_as_neither(tmp_path: Path) -> None:
    target = tmp_path / "empty.json"
    target.write_bytes(b"")
    assert gate.detect_profile(target) is None


def test_an_undetectable_profile_is_refused_rather_than_defaulted(tmp_path: Path) -> None:
    target = tmp_path / "image.bin"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")
    report = _run(target)
    verdict = report.files[0]
    assert verdict.gradeable is False
    assert verdict.grade is None
    assert report.exit_code == gate.EXIT_NOT_GRADEABLE
    assert "no assessment profile applies" in (verdict.refusal or "")


# --- the letter is the committed one ------------------------------------------------------------


def test_a_conforming_json_file_clears_the_gate(tmp_path: Path) -> None:
    report = _run(_write_json(tmp_path, _json_document()), fail_on="error", min_grade="B")
    verdict = report.files[0]
    assert verdict.grade in {"A", "B"}
    assert verdict.failures == ()
    assert report.exit_code == gate.EXIT_PASS


def test_a_superseded_template_version_fails_the_gate(tmp_path: Path) -> None:
    """The finding this project published first: a `3.0` where CMS specifies `3.0.0`."""
    report = _run(_write_json(tmp_path, _json_document(version="3.0")), fail_on="error")
    verdict = report.files[0]
    codes = {finding.code for finding in verdict.findings}
    assert any("VERSION" in code for code in codes), codes
    assert report.exit_code == gate.EXIT_GATE_FAILED
    assert verdict.failures


def test_the_csv_profile_carries_its_own_committed_policy_name(tmp_path: Path) -> None:
    report = _run(_write_csv(tmp_path))
    verdict = report.files[0]
    assert verdict.profile == "csv"
    assert verdict.grade_policy_version == CSV_GRADE_POLICY_VERSION


def test_the_gate_prints_the_same_policy_fingerprint_the_site_publishes(tmp_path: Path) -> None:
    """The gate must not mint a second scale; it reuses the committed rule table verbatim."""
    report = _run(_write_json(tmp_path, _json_document()))
    verdict = report.files[0]
    assert verdict.grade_policy_version == GRADE_POLICY_VERSION
    assert verdict.grade_policy_fingerprint == GRADE_POLICY_FINGERPRINT

    published = json.loads(
        (REPO_ROOT / "data" / "cohorts" / "2026-08-19.comparison.json").read_text(encoding="utf-8")
    )
    fingerprints = {row["grade"]["policy_fingerprint"] for row in published["files"]}
    assert fingerprints == {GRADE_POLICY_FINGERPRINT}


def test_an_unknown_profile_name_is_an_error_not_a_json_default() -> None:
    """Substituting the JSON policy for an unrecognised profile is the defect class this repo
    keeps finding: a failed lookup rendered as a specific, plausible value."""
    scorecard = {
        name: {"name": name, "status": "OBSERVED", "findings": []}
        for name in ("conformance", "completeness", "interpretability", "freshness")
    }
    with pytest.raises(CohortError, match="cms-hospital-xml-v9"):
        grade_local_evidence(scorecard, profile="cms-hospital-xml-v9")


# --- a refusal is not a grade -------------------------------------------------------------------


def test_a_file_that_cannot_be_streamed_is_exit_two_and_carries_no_letter(tmp_path: Path) -> None:
    target = tmp_path / "truncated.json"
    target.write_text('{"hospital_name": "Example", "standard_charge_information": [{', "utf-8")
    report = _run(target, fail_on="error", min_grade="A")
    verdict = report.files[0]
    assert verdict.scan_completed is False
    assert verdict.gradeable is False
    assert verdict.grade is None
    assert verdict.failures == ()
    assert report.exit_code == gate.EXIT_NOT_GRADEABLE


def test_a_zip_holding_two_documents_is_refused_with_the_reason(tmp_path: Path) -> None:
    archive = tmp_path / "prices.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("a.json", json.dumps(_json_document()))
        bundle.writestr("b.json", json.dumps(_json_document()))
    report = _run(archive)
    verdict = report.files[0]
    assert verdict.gradeable is False
    assert verdict.grade is None
    assert verdict.refusal
    assert report.exit_code == gate.EXIT_NOT_GRADEABLE


def test_a_zip_holding_one_document_is_still_refused_by_the_gate(tmp_path: Path) -> None:
    """`inspect` lifts the member out; the gate refuses. A hospital posts the document, not an
    archive of it, so a container is a finding about the publication, not a thing to grade."""
    archive = tmp_path / "prices.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("a.json", json.dumps(_json_document()))
    report = _run(archive)
    assert report.files[0].gradeable is False
    assert report.exit_code == gate.EXIT_NOT_GRADEABLE


def test_a_missing_path_is_not_gradeable(tmp_path: Path) -> None:
    report = _run(tmp_path / "absent.json")
    assert report.exit_code == gate.EXIT_NOT_GRADEABLE
    assert report.files[0].refusal


def test_not_gradeable_outranks_a_failed_threshold_across_files(tmp_path: Path) -> None:
    good = _write_json(tmp_path, _json_document(), name="good.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    report = gate.run_gate(
        [good, broken], profile="auto", as_of=AS_OF, min_grade="A", fail_on="error"
    )
    assert report.exit_code == gate.EXIT_NOT_GRADEABLE


def test_run_gate_refuses_an_empty_file_list() -> None:
    with pytest.raises(gate.GateError):
        gate.run_gate([], profile="auto", as_of=AS_OF, min_grade="none", fail_on="never")


def test_a_not_graded_letter_has_no_place_in_the_a_to_f_order() -> None:
    with pytest.raises(gate.GateError):
        gate._grade_below("NOT_GRADED", "B")


# --- thresholds ---------------------------------------------------------------------------------


def test_fail_on_never_reports_without_gating(tmp_path: Path) -> None:
    report = _run(_write_json(tmp_path, _json_document(version="3.0")))
    assert report.files[0].findings
    assert report.exit_code == gate.EXIT_PASS


def test_fail_on_warning_also_catches_errors(tmp_path: Path) -> None:
    report = _run(_write_json(tmp_path, _json_document(version="3.0")), fail_on="warning")
    assert report.exit_code == gate.EXIT_GATE_FAILED


def test_min_grade_a_fails_a_file_graded_b(tmp_path: Path) -> None:
    stale = _write_json(tmp_path, _json_document(updated="2020-01-01"))
    lenient = _run(stale)
    assert lenient.files[0].grade in {"B", "C", "D", "F"}
    strict = _run(stale, min_grade="A")
    assert strict.exit_code == gate.EXIT_GATE_FAILED
    assert "below the required minimum A" in strict.files[0].failures[0]


def test_min_grade_f_gates_nothing_that_was_graded(tmp_path: Path) -> None:
    report = _run(_write_json(tmp_path, _json_document(updated="2020-01-01")), min_grade="F")
    assert report.exit_code == gate.EXIT_PASS


# --- absence is named, never scored --------------------------------------------------------------


def test_a_not_assessed_dimension_is_named_in_the_report_and_the_summary(tmp_path: Path) -> None:
    """An empty charge array leaves interpretability unassessed. The letter alone cannot say
    that, so the report carries the dimension's name and the summary prints it."""
    document = _json_document()
    document["standard_charge_information"] = []
    report = _run(_write_json(tmp_path, document))
    verdict = report.files[0]
    assert verdict.not_assessed_dimensions == ("interpretability",)
    summary = gate.job_summary(report)
    assert "**Not assessed:** interpretability" in summary
    assert "never read as a pass" in summary
    assert json.loads(gate.report_json(report))["files"][0]["not_assessed_dimensions"] == [
        "interpretability"
    ]


def test_an_unassessed_dimension_with_no_finding_is_invisible_to_fail_on_but_lowers_the_grade() -> (
    None
):
    """The rule `--fail-on` cannot enforce, stated as a test rather than as a docstring.

    A dimension with status NOT_ASSESSED and no findings produces nothing for a severity filter
    to match, so no `--fail-on` setting can stop it -- and the committed rule table counts it as
    a failed dimension, so `--min-grade` can. Both halves are asserted here, because the harm is
    a reader concluding from a clean `--fail-on error` run that nothing was missed.
    """
    clean = {
        name: {"name": name, "status": "OBSERVED", "findings": []}
        for name in ("conformance", "completeness", "interpretability", "freshness")
    }
    assert grade_local_evidence(clean, profile="cms-hospital-json-v3").grade == "A"

    unassessed = dict(clean)
    unassessed["freshness"] = {"name": "freshness", "status": "NOT_ASSESSED", "findings": []}
    lowered = grade_local_evidence(unassessed, profile="cms-hospital-json-v3")
    assert lowered.grade == "C"
    assert lowered.error_dimensions == ("freshness",)
    # There is nothing here for a severity threshold to see.
    assert gate._not_assessed(unassessed) == ("freshness",)


def test_min_grade_catches_the_unassessed_dimension_that_fail_on_cannot(tmp_path: Path) -> None:
    document = _json_document()
    document["standard_charge_information"] = []
    target = _write_json(tmp_path, document)
    assert _run(target).files[0].grade == "D"
    assert _run(target, min_grade="C").exit_code == gate.EXIT_GATE_FAILED


def test_the_summary_states_that_clearing_the_gate_certifies_nothing(tmp_path: Path) -> None:
    report = _run(_write_json(tmp_path, _json_document()))
    summary = gate.job_summary(report)
    assert gate.NON_CERTIFICATION_NOTICE in summary
    assert gate.NON_CERTIFICATION_NOTICE in gate.human_report(report)


def test_the_summary_never_calls_a_cleared_file_compliant(tmp_path: Path) -> None:
    """RESPONSIBLE-TECH audit: the compliance vocabulary never appears as a claim. The one
    permitted use is the disclaimer itself, which is removed before the check so that its
    presence cannot be what makes this test pass."""
    report = _run(_write_json(tmp_path, _json_document()), fail_on="error")
    summary = gate.job_summary(report).lower()
    assert gate.NON_CERTIFICATION_NOTICE.lower() in summary
    body = summary.replace(gate.NON_CERTIFICATION_NOTICE.lower(), "")
    for word in ("compliant", "compliance", "certif", "valid", "approved", "endorse"):
        assert word not in body, word


def test_a_refused_file_is_summarised_as_not_an_f(tmp_path: Path) -> None:
    target = tmp_path / "truncated.json"
    target.write_text('{"standard_charge_information": [{', "utf-8")
    summary = gate.job_summary(_run(target))
    assert "Not gradeable" in summary
    assert "This is not an F" in summary


# --- renderings ----------------------------------------------------------------------------------


def test_annotations_use_the_github_level_matching_each_severity(tmp_path: Path) -> None:
    report = _run(_write_json(tmp_path, _json_document(version="3.0")), fail_on="error")
    lines = gate.github_annotations(report)
    assert lines
    assert any(line.startswith("::error ") for line in lines)
    assert all(line.startswith(("::error ", "::warning ", "::notice ")) for line in lines)
    assert any("title=CMS_V3" in line for line in lines)


def test_annotation_escaping_protects_the_command_syntax() -> None:
    assert gate._escape_command_data("100%\nnext") == "100%25%0Anext"
    assert gate._escape_command_property("a:b,c") == "a%3Ab%2Cc"


def test_the_json_report_round_trips(tmp_path: Path) -> None:
    report = _run(_write_json(tmp_path, _json_document()), fail_on="error", min_grade="B")
    payload = json.loads(gate.report_json(report))
    assert payload["exit_code"] == gate.EXIT_PASS
    assert payload["fail_on"] == "error"
    assert payload["min_grade"] == "B"
    assert payload["files"][0]["grade"] in {"A", "B"}
    assert payload["notice"] == gate.NON_CERTIFICATION_NOTICE


# --- the CLI surface -----------------------------------------------------------------------------


def test_cli_gate_returns_the_gate_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write_json(tmp_path, _json_document(version="3.0"))
    code = main(["gate", str(target), "--as-of", AS_OF.isoformat(), "--fail-on", "error"])
    capsys.readouterr()
    assert code == gate.EXIT_GATE_FAILED


def test_cli_gate_writes_annotations_a_summary_and_a_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write_json(tmp_path, _json_document(version="3.0"))
    summary = tmp_path / "summary.md"
    report = tmp_path / "report.json"
    code = main(
        [
            "gate",
            str(target),
            "--as-of",
            AS_OF.isoformat(),
            "--github",
            "--summary",
            str(summary),
            "--report",
            str(report),
        ]
    )
    captured = capsys.readouterr()
    assert code == gate.EXIT_PASS
    assert captured.out.startswith("::")
    assert "mrf-honest" in summary.read_text(encoding="utf-8")
    assert json.loads(report.read_text(encoding="utf-8"))["files"][0]["profile"] == "json"


def test_cli_gate_json_format_emits_one_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write_json(tmp_path, _json_document())
    code = main(["gate", str(target), "--as-of", AS_OF.isoformat(), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == gate.EXIT_PASS
    assert payload["files"][0]["path"] == str(target)


def test_cli_gate_accepts_a_publisher_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write_csv(tmp_path)
    code = main(
        ["gate", str(target), "--as-of", AS_OF.isoformat(), "--publisher-id", "example-health"]
    )
    capsys.readouterr()
    assert code == gate.EXIT_PASS


def test_cli_gate_human_output_names_the_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write_json(tmp_path, _json_document())
    main(["gate", str(target), "--as-of", AS_OF.isoformat()])
    out = capsys.readouterr().out
    assert GRADE_POLICY_VERSION in out


def test_cli_gate_defaults_to_today_when_no_as_of_is_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write_json(tmp_path, _json_document(updated=date.today().isoformat()))
    code = main(["gate", str(target)])
    capsys.readouterr()
    assert code == gate.EXIT_PASS


# --- the two packagings actually invoke this CLI --------------------------------------------


def test_the_action_invokes_a_command_this_cli_has() -> None:
    """A packaging that calls a verb the CLI does not have is a gate that cannot fire."""
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    script = "\n".join(
        str(step.get("run", "")) for step in action["runs"]["steps"] if "run" in step
    )
    assert "mrf_honest gate" in script or "mrf-honest gate" in script
    declared = set(action["inputs"])
    assert {"path", "profile", "as-of", "min-grade", "fail-on"} <= declared


def test_the_action_offers_only_choices_the_cli_accepts() -> None:
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
    assert action["inputs"]["profile"]["default"] in gate.PROFILE_CHOICES
    assert action["inputs"]["min-grade"]["default"] in gate.MIN_GRADE_CHOICES
    assert action["inputs"]["fail-on"]["default"] in gate.FAIL_ON_CHOICES


def test_the_pre_commit_hook_matches_both_file_kinds() -> None:
    """`types: [json, csv]` is an AND in pre-commit and would match nothing. This is the
    "gate that cannot fail" shape, so the hook is asserted to use `types_or`."""
    hooks = yaml.safe_load((REPO_ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))
    assert len(hooks) == 1
    hook = hooks[0]
    assert "types" not in hook
    assert set(hook["types_or"]) == {"json", "csv"}
    assert "gate" in hook["entry"]
    assert hook["pass_filenames"] is not False


def test_python_dash_m_runs_the_cli_the_action_invokes(tmp_path: Path) -> None:
    """``action.yml`` runs the checked-out source with ``python -m mrf_honest``. Without a
    ``__main__`` module that invocation fails with "cannot be directly executed", and the
    packaging tests above would still pass, because they only read the file's text."""
    from mrf_honest import __main__ as module

    assert module.main is main

    target = _write_json(tmp_path, _json_document())
    # S603: the interpreter is this test's own, and the only non-literal argument is a
    # pytest tmp_path this test wrote a moment ago.
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "mrf_honest",
            "gate",
            str(target),
            "--as-of",
            AS_OF.isoformat(),
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == gate.EXIT_PASS, completed.stderr
    assert json.loads(completed.stdout)["files"][0]["grade"] == "A"
