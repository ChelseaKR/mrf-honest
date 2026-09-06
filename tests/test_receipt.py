"""Receipts, badges, and `mrf-honest verify` (#70).

The properties, in the order a regression in them would matter:

1. **A receipt for a row nobody can re-derive says so.** Seven of the committed rows have no
   verified body; a receipt that looked like the others would send a reader to reproduce
   something that was never derived from bytes, and the resulting mismatch would read as a
   fact about the hospital's file.
2. **Exit 2 is never evidence about the file.** A hash mismatch, an unknown policy version, a
   receipt version this build cannot read, and a receipt marked not re-derivable are all
   "could not check", never "the grade differs".
3. **A receipt reproduces.** Real bytes, real inspector, real committed policy, offline.
4. **The published receipt agrees with the published row**, gated on the deploy path.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from mrf_honest import __version__
from mrf_honest.cli import main
from mrf_honest.dataset import missing_exports
from mrf_honest.inspect import inspect_hospital_file
from mrf_honest.receipt import (
    EXIT_CANNOT_CHECK,
    EXIT_DIFFERS,
    EXIT_REPRODUCED,
    RECEIPT_VERSION,
    Receipt,
    receipt_from_row,
    receipts_for,
    verify_receipt,
)
from mrf_honest.site import BADGE_FILL, MIN_CONTRAST, badge_contrast, badge_svg, render_site

ROOT = Path(__file__).resolve().parents[1]
COHORTS = ROOT / "data" / "cohorts"
PUBLISHED = sorted(COHORTS.glob("*.comparison.json"))
AS_OF = date(2026, 8, 19)


def test_at_least_one_cohort_is_published() -> None:
    """An empty glob would make every case below vacuous, which is the shape this repository
    keeps finding in its own gates."""
    assert PUBLISHED, f"no committed comparison documents under {COHORTS}"


def _comparisons() -> list[dict[str, object]]:
    """The comparisons the publish workflow renders: the newest of each profile.

    Mirrors `_rendered_comparisons` in `test_published_claims.py`. The full glob cannot be
    used: two cohorts of the same profile share file slugs, and `render_site` refuses that
    collision by design -- as, now, does `receipts_for`.
    """
    newest: dict[str, tuple[str, Path]] = {}
    for path in PUBLISHED:
        document = json.loads(path.read_text(encoding="utf-8"))
        cohort = document["cohort"]
        profile = str(cohort["comparison_scope"]["profile"])
        key = (str(cohort["as_of"]), path)
        if profile not in newest or key > newest[profile]:
            newest[profile] = key
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for _, path in (newest[profile] for profile in sorted(newest))
    ]


def _payers() -> list[dict[str, object]]:
    return [
        {
            "payer_name": "Acme",
            "plan_name": "PPO",
            "methodology": "fee schedule",
            "standard_charge_dollar": 800,
        }
    ]


def _document(*, version: str = "3.0.0", updated: str = "2026-08-01") -> dict[str, object]:
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


def _write(tmp_path: Path, document: object, name: str = "prices.json") -> Path:
    target = tmp_path / name
    target.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    return target


def _receipt_for_file(path: Path, **overrides: object) -> dict[str, object]:
    """A receipt describing a real local file, built the way `compare` builds one.

    Constructed from a synthetic row rather than lifted from a committed cohort on purpose:
    the committed rows describe bytes this repository deliberately does not ship, so a test
    that verified one of them could only ever assert a hash mismatch.
    """
    inspection = inspect_hospital_file(path, None, as_of=AS_OF)
    payload = inspection.to_dict()
    scorecard = payload["scorecard"]
    assert isinstance(scorecard, dict)
    from mrf_honest.cohort import grade_local_evidence

    grade = grade_local_evidence(scorecard, profile="cms-hospital-json-v3")
    row: dict[str, object] = {
        "as_of": AS_OF.isoformat(),
        "assessment_id": "0" * 64,
        "content_sha256": payload["source_sha256"],
        "coverage": {
            "inspection_performed": True,
            "inspection_scan_completed": True,
            "network_attempted": True,
            "targeted": True,
            "verified_body_available": True,
        },
        "dimensions": {
            name: dict(scorecard[name])
            for name in ("conformance", "completeness", "interpretability", "freshness")
        },
        "grade": grade.to_dict(),
        "location_id": "example-main",
        "publisher_id": "example-health",
        "publisher_name": "Example Hospital",
        "requested_url": "https://example.org/prices.json",
        "size_bytes": payload["source_size"],
        "slug": "example-health/example-main",
    }
    comparison = {"cohort": {"comparison_scope": {"profile": "cms-hospital-json-v3"}}}
    receipt = receipt_from_row(row, comparison).to_dict()
    receipt.update(overrides)
    return receipt


# --- 3. a receipt reproduces ------------------------------------------------------------


def test_a_receipt_reproduces_its_grade_and_every_finding(tmp_path: Path) -> None:
    path = _write(tmp_path, _document())
    receipt = _receipt_for_file(path)
    outcome = verify_receipt(receipt, path)
    assert outcome.exit_code == EXIT_REPRODUCED, outcome.summary
    assert outcome.observed_grade == receipt["grade"]
    assert outcome.differences == ()


def test_a_receipt_reproduces_a_file_that_has_findings(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(version="3.0"))
    receipt = _receipt_for_file(path)
    assert receipt["findings"], "the fixture was meant to produce findings"
    assert verify_receipt(receipt, path).exit_code == EXIT_REPRODUCED


def test_the_receipt_records_the_tool_version_and_the_committed_policy(tmp_path: Path) -> None:
    receipt = _receipt_for_file(_write(tmp_path, _document()))
    assert receipt["tool_version"] == __version__
    assert receipt["grade_policy_version"] == "cms-hospital-json-v3-file-grade-v1"
    assert re.fullmatch(r"[0-9a-f]{64}", str(receipt["grade_policy_fingerprint"]))


# --- 2. exit 2 is never evidence about the file --------------------------------------------


def test_a_one_byte_change_is_exit_two_with_both_hashes(tmp_path: Path) -> None:
    path = _write(tmp_path, _document())
    receipt = _receipt_for_file(path)
    path.write_bytes(path.read_bytes() + b" ")
    outcome = verify_receipt(receipt, path)
    assert outcome.exit_code == EXIT_CANNOT_CHECK
    assert "not the bytes the receipt describes" in outcome.summary
    assert any(str(receipt["content_sha256"]) in line for line in outcome.differences)
    assert any(str(outcome.observed_sha256) in line for line in outcome.differences)


def test_a_policy_version_this_build_lacks_is_exit_two_naming_the_version(tmp_path: Path) -> None:
    path = _write(tmp_path, _document())
    receipt = _receipt_for_file(path, grade_policy_version="cms-hospital-json-v3-file-grade-v9")
    outcome = verify_receipt(receipt, path)
    assert outcome.exit_code == EXIT_CANNOT_CHECK
    assert "cms-hospital-json-v3-file-grade-v9" in outcome.summary
    assert "not comparable" in outcome.summary


def test_a_receipt_version_this_build_cannot_read_is_exit_two(tmp_path: Path) -> None:
    path = _write(tmp_path, _document())
    outcome = verify_receipt(_receipt_for_file(path, receipt_version=99), path)
    assert outcome.exit_code == EXIT_CANNOT_CHECK
    assert "99" in outcome.summary


def test_a_profile_this_build_cannot_inspect_is_exit_two(tmp_path: Path) -> None:
    path = _write(tmp_path, _document())
    outcome = verify_receipt(_receipt_for_file(path, profile="cms-hospital-xml-v9"), path)
    assert outcome.exit_code == EXIT_CANNOT_CHECK
    assert "cms-hospital-xml-v9" in outcome.summary


def test_a_missing_file_is_exit_two(tmp_path: Path) -> None:
    receipt = _receipt_for_file(_write(tmp_path, _document()))
    assert verify_receipt(receipt, tmp_path / "absent.json").exit_code == EXIT_CANNOT_CHECK


def test_a_grade_that_moved_is_exit_one_with_the_difference_named(tmp_path: Path) -> None:
    """The one case that *is* a statement about a difference: the bytes are the bytes the
    receipt describes, and the re-derivation disagrees."""
    path = _write(tmp_path, _document())
    receipt = _receipt_for_file(path, grade="F")
    outcome = verify_receipt(receipt, path)
    assert outcome.exit_code == EXIT_DIFFERS
    assert any("grade: receipt F" in line for line in outcome.differences)


def test_a_finding_missing_from_the_receipt_is_exit_one(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(version="3.0"))
    receipt = _receipt_for_file(path, findings=[])
    outcome = verify_receipt(receipt, path)
    assert outcome.exit_code == EXIT_DIFFERS
    assert any("re-derived but not in the receipt" in line for line in outcome.differences)


def test_a_finding_only_in_the_receipt_is_exit_one(tmp_path: Path) -> None:
    path = _write(tmp_path, _document())
    receipt = _receipt_for_file(
        path,
        findings=[
            {
                "code": "CMS_V3_VERSION_UNEXPECTED",
                "dimension": "conformance",
                "occurrences": 1,
                "severity": "ERROR",
            }
        ],
    )
    outcome = verify_receipt(receipt, path)
    assert outcome.exit_code == EXIT_DIFFERS
    assert any("in the receipt but not re-derived" in line for line in outcome.differences)


# --- 1. a row nobody can re-derive says so --------------------------------------------------


def test_a_row_with_no_verified_body_is_issued_as_not_re_derivable() -> None:
    receipts = receipts_for(_comparisons())
    unusable = [r for r in receipts.values() if not r.re_derivable]
    assert unusable, "the committed cohorts hold rows with no verified body"
    for receipt in unusable:
        assert receipt.content_sha256 is None or receipt.not_re_derivable_reason
        assert receipt.not_re_derivable_reason
        # The published grade is still recorded -- it is what was published -- but the receipt
        # says plainly that nobody can reproduce it.
        assert receipt.grade


def test_every_committed_row_with_bytes_is_issued_as_re_derivable() -> None:
    for comparison in _comparisons():
        for row in comparison["files"]:
            receipt = receipt_from_row(row, comparison)
            has_bytes = bool(row.get("content_sha256"))
            scanned = row["coverage"].get("inspection_scan_completed") is True
            assert receipt.re_derivable is (has_bytes and scanned), receipt.slug


def test_verify_refuses_a_not_re_derivable_receipt_by_name(tmp_path: Path) -> None:
    receipts = receipts_for(_comparisons())
    unusable = next(r for r in receipts.values() if not r.re_derivable)
    path = _write(tmp_path, _document())
    outcome = verify_receipt(unusable.to_dict(), path)
    assert outcome.exit_code == EXIT_CANNOT_CHECK
    assert "not re-derivable" in outcome.summary
    assert str(unusable.not_re_derivable_reason) in outcome.summary


def test_the_two_reasons_a_row_is_not_re_derivable_are_told_apart() -> None:
    reasons = {
        r.not_re_derivable_reason
        for r in receipts_for(_comparisons()).values()
        if not r.re_derivable
    }
    assert any("never retrieved" in str(reason) for reason in reasons)
    assert any("no verified body" in str(reason) for reason in reasons)


# --- 4. the published receipt agrees with the published row ---------------------------------


def test_the_render_writes_a_receipt_and_a_badge_for_every_published_row(tmp_path: Path) -> None:
    comparisons = _comparisons()
    out = tmp_path / "site"
    render_site(comparisons, out)
    rows = [row for comparison in comparisons for row in comparison["files"]]
    for row in rows:
        slug = str(row["slug"])
        assert (out / "api" / "receipt" / f"{slug}.json").is_file(), slug
        assert (out / "badge" / f"{slug}.svg").is_file(), slug
    assert missing_exports(comparisons, out) == []


def test_the_published_receipt_states_the_grade_the_published_row_states(tmp_path: Path) -> None:
    comparisons = _comparisons()
    out = tmp_path / "site"
    render_site(comparisons, out)
    for comparison in comparisons:
        for row in comparison["files"]:
            receipt = json.loads(
                (out / "api" / "receipt" / f"{row['slug']}.json").read_text(encoding="utf-8")
            )
            assert receipt["grade"] == row["grade"]["grade"], row["slug"]
            assert receipt["content_sha256"] == row.get("content_sha256"), row["slug"]


def test_a_receipt_disagreeing_with_its_row_fails_the_deploy_gate(tmp_path: Path) -> None:
    """`missing_exports` is what the deploy path calls. A receipt that exists but states a
    different grade must fail it -- checking only that the file exists is the easy half."""
    comparisons = _comparisons()
    out = tmp_path / "site"
    render_site(comparisons, out)
    assert missing_exports(comparisons, out) == []
    slug = str(comparisons[0]["files"][0]["slug"])
    target = out / "api" / "receipt" / f"{slug}.json"
    tampered = json.loads(target.read_text(encoding="utf-8"))
    tampered["grade"] = "A" if tampered["grade"] != "A" else "F"
    target.write_text(json.dumps(tampered), encoding="utf-8")
    problems = missing_exports(comparisons, out)
    assert any(slug in problem and "states grade" in problem for problem in problems), problems


def test_a_deleted_badge_fails_the_deploy_gate(tmp_path: Path) -> None:
    comparisons = _comparisons()
    out = tmp_path / "site"
    render_site(comparisons, out)
    slug = str(comparisons[0]["files"][0]["slug"])
    (out / "badge" / f"{slug}.svg").unlink()
    assert any("was not written" in problem for problem in missing_exports(comparisons, out))


def test_publishing_receipts_does_not_change_the_page_count(tmp_path: Path) -> None:
    """The badges are SVG documents no page embeds, so the Lighthouse job -- which enumerates
    `*.html` -- audits the same set of pages, and `perf/baseline.json` still describes it."""
    comparisons = _comparisons()
    out = tmp_path / "site"
    render_site(comparisons, out)
    baseline = json.loads((ROOT / "perf" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["meta"]["pages_audited"] == len(list(out.rglob("*.html")))


# --- the badge ------------------------------------------------------------------------------


def test_the_badge_is_labelled_for_a_screen_reader(tmp_path: Path) -> None:
    receipt = _receipt_for_file(_write(tmp_path, _document()))
    svg = badge_svg(receipt)
    assert 'role="img"' in svg
    assert "<title>" in svg
    assert 'aria-label="' in svg


def test_the_badge_title_carries_the_whole_claim(tmp_path: Path) -> None:
    receipt = _receipt_for_file(_write(tmp_path, _document()))
    svg = badge_svg(receipt)
    assert str(receipt["grade_policy_version"]) in svg
    assert str(receipt["as_of"]) in svg
    assert "Not a certificate of compliance" in svg


@pytest.mark.parametrize("grade", sorted(BADGE_FILL))
def test_every_badge_fill_clears_the_contrast_threshold(grade: str) -> None:
    """The badge's palette is the site's palette. A token changed for the page must not take
    the badge's text below 4.5:1 unnoticed, so the exact pairs the SVG uses are asserted here
    as well as in the site's own table."""
    assert badge_contrast({"grade": grade}) >= MIN_CONTRAST


def test_a_not_graded_badge_says_not_graded_rather_than_showing_a_letter() -> None:
    svg = badge_svg({"grade": "NOT_GRADED", "as_of": "2026-08-19"})
    assert ">not graded<" in svg
    assert ">F<" not in svg


def test_the_badge_escapes_a_publisher_name_that_carries_markup() -> None:
    svg = badge_svg({"grade": "A", "publisher_name": '<script>"x"</script>', "as_of": "x"})
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


# --- the CLI --------------------------------------------------------------------------------


def test_cli_verify_returns_the_verify_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, _document())
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(_receipt_for_file(path)), encoding="utf-8")
    code = main(["verify", str(receipt_path), str(path)])
    assert "reproduced" in capsys.readouterr().out
    assert code == EXIT_REPRODUCED


def test_cli_verify_reports_a_hash_mismatch_as_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, _document())
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(_receipt_for_file(path)), encoding="utf-8")
    path.write_bytes(path.read_bytes() + b" ")
    code = main(["verify", str(receipt_path), str(path)])
    capsys.readouterr()
    assert code == EXIT_CANNOT_CHECK


def test_cli_verify_json_carries_the_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, _document())
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(_receipt_for_file(path)), encoding="utf-8")
    main(["verify", str(receipt_path), str(path), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == EXIT_REPRODUCED
    assert "not a certificate" in str(payload["notice"]).lower()


def test_cli_verify_refuses_a_receipt_that_is_not_an_object(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("[]", encoding="utf-8")
    code = main(["verify", str(receipt_path), str(_write(tmp_path, _document()))])
    assert "must be a JSON object" in capsys.readouterr().err
    assert code == 2


def test_the_receipt_dataclass_round_trips_its_version() -> None:
    receipt = Receipt(
        receipt_version=RECEIPT_VERSION,
        assessment_id="a",
        slug="s/l",
        publisher_id="p",
        publisher_name="P",
        location_id="l",
        requested_url="https://example.org/x",
        content_sha256=None,
        size_bytes=None,
        as_of="2026-08-19",
        profile="cms-hospital-json-v3",
        grade="NOT_GRADED",
        grade_reason="r",
        grade_policy_version="v",
        grade_policy_fingerprint="f",
        tool_version=__version__,
        findings=(),
        re_derivable=False,
        not_re_derivable_reason="because",
    )
    assert receipt.to_dict()["receipt_version"] == RECEIPT_VERSION
    assert receipt.to_dict()["re_derivable"] is False
