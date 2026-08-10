"""Bounded, deterministic inspection of local CMS hospital JSON v3 files.

This module reports observable file properties.  It does not run CMS's validator, make a legal
compliance determination, test a remote URL, or collapse unlike dimensions into a numerical rank.
The selected checks are grounded in CMS's v3.0 JSON data dictionary and 45 CFR 180.50; every
finding carries the applicable primary-source link.

The charge array is streamed and only counters, bounded problem samples, and one finding per code
are retained.  Peak memory therefore follows the largest single charge item plus the stream
reader's fixed-size buffers, rather than the size of the source file.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from mrf_honest.stream import StreamError, StreamStats, stream_array_items
from mrf_honest.types import PublisherRef

CMS_JSON_DICTIONARY = (
    "https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/README.md"
)
CMS_V3_SCHEMA = (
    "https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/"
    "schemas/V3.0.0_Hospital_price_transparency_schema.json"
)
CFR_180_50 = (
    "https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/"
    "subpart-B/section-180.50"
)

ACCEPTED_SETTINGS = frozenset({"inpatient", "outpatient", "both"})
ACCEPTED_METHODOLOGIES = frozenset(
    {"case rate", "fee schedule", "percent of total billed charges", "per diem", "other"}
)

type DimensionName = Literal[
    "retrievability", "conformance", "completeness", "interpretability", "freshness"
]
type DimensionStatus = Literal["OBSERVED", "FINDINGS", "NOT_ASSESSED"]
type FindingSeverity = Literal["INFO", "WARNING", "ERROR"]
type JSONValue = bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"] | None

_ARRAY_KEY = "standard_charge_information"
_REQUIRED_ENVELOPE_FIELDS = (
    "hospital_name",
    "last_updated_on",
    "version",
    "location_name",
    "hospital_address",
    "type_2_npi",
    "license_information",
    "attestation",
)
_OPTIONAL_ENVELOPE_FIELDS = ("financial_aid_policy",)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PROBLEM_SAMPLE_LIMIT = 20
_PROBLEM_TEXT_LIMIT = 300
_READ_CHUNK = 1 << 20


@dataclass(frozen=True)
class Finding:
    """One deduplicated, source-cited observation about a scorecard dimension."""

    code: str
    dimension: DimensionName
    severity: FindingSeverity
    message: str
    citations: tuple[str, ...]
    occurrences: int = 1


@dataclass(frozen=True)
class FindingDefinition:
    """Stable catalog entry used to explain a finding code without inspecting a file."""

    code: str
    dimension: DimensionName
    severity: FindingSeverity
    description: str
    citations: tuple[str, ...]


def _definition(
    code: str,
    dimension: DimensionName,
    severity: FindingSeverity,
    description: str,
    *citations: str,
) -> FindingDefinition:
    return FindingDefinition(code, dimension, severity, description, citations)


def _build_finding_catalog() -> Mapping[str, FindingDefinition]:
    schema_rule = (CMS_V3_SCHEMA, CFR_180_50)
    dictionary_rule = (CMS_JSON_DICTIONARY, CFR_180_50)
    definitions = [
        _definition(
            "CMS_V3_ATTESTATION_NOT_CONFIRMED",
            "conformance",
            "WARNING",
            "The v3 attestation is explicitly not confirmed.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_V3_CHARGE_GROUP_NOT_OBJECT",
            "conformance",
            "ERROR",
            "A standard charge group is not a JSON object.",
            CMS_V3_SCHEMA,
        ),
        _definition(
            "CMS_V3_CHARGE_VALUE_MISSING",
            "completeness",
            "ERROR",
            "A charge group contains no gross, cash, or payer-specific charge.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_CODE_TYPE_MISSING",
            "completeness",
            "ERROR",
            "A code information entry has no usable code type.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_CODE_VALUE_MISSING",
            "completeness",
            "ERROR",
            "A code information entry has no usable code value.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_DERIVED_RATE_COUNT_MISSING",
            "completeness",
            "ERROR",
            "A percentage or algorithm rate has no allowed-amount count.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_V3_ENVELOPE_STANDARD_CHARGE_INFORMATION_MISSING",
            "conformance",
            "ERROR",
            "The required standard_charge_information array is absent or unusable.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_ITEM_CODE_INFORMATION_MISSING",
            "completeness",
            "ERROR",
            "An item has no non-empty code_information array.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_ITEM_DESCRIPTION_MISSING",
            "completeness",
            "ERROR",
            "An item has no usable description.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_ITEM_STANDARD_CHARGES_MISSING",
            "completeness",
            "ERROR",
            "An item has no non-empty standard_charges array.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_LAST_UPDATED_ON_INVALID",
            "conformance",
            "ERROR",
            "last_updated_on is not a valid ISO YYYY-MM-DD date.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_METHODOLOGY_INVALID",
            "conformance",
            "ERROR",
            "A payer methodology is outside the CMS v3 accepted set.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_V3_OTHER_METHODOLOGY_NOTES_MISSING",
            "completeness",
            "ERROR",
            "An 'other' methodology has no explanatory payer notes.",
            CMS_JSON_DICTIONARY,
            CMS_V3_SCHEMA,
        ),
        _definition(
            "CMS_V3_PAYERS_INFORMATION_INVALID",
            "conformance",
            "ERROR",
            "A present payers_information value is not a non-empty array.",
            CMS_V3_SCHEMA,
        ),
        _definition(
            "CMS_V3_PAYER_CHARGE_MISSING",
            "completeness",
            "ERROR",
            "A payer entry has no dollar, percentage, or algorithm charge.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_PAYER_RATE_NOT_OBJECT",
            "conformance",
            "ERROR",
            "A payer rate entry is not a JSON object.",
            CMS_V3_SCHEMA,
        ),
        _definition(
            "CMS_V3_SETTING_INVALID",
            "conformance",
            "ERROR",
            "A charge setting is outside the CMS v3 accepted set.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_V3_STANDARD_CHARGE_INFORMATION_EMPTY",
            "completeness",
            "ERROR",
            "The charge array contains no usable item objects.",
            *schema_rule,
        ),
        _definition(
            "CMS_V3_VERSION_UNEXPECTED",
            "conformance",
            "ERROR",
            "The template version is not the v3.0.0 version implemented here.",
            CMS_V3_SCHEMA,
            CMS_JSON_DICTIONARY,
        ),
        _definition(
            "CMS_V3_ZERO_COUNT_NOTES_MISSING",
            "completeness",
            "ERROR",
            "A derived rate with count zero has no explanatory payer notes.",
            CMS_JSON_DICTIONARY,
            CMS_V3_SCHEMA,
        ),
        _definition(
            "FRESHNESS_ANNUAL_UPDATE_OVERDUE",
            "freshness",
            "WARNING",
            "The source publication date is more than one year before as_of.",
            CFR_180_50,
        ),
        _definition(
            "FRESHNESS_DATE_IN_FUTURE",
            "freshness",
            "WARNING",
            "The source publication date is after as_of.",
            CFR_180_50,
        ),
        _definition(
            "FRESHNESS_DATE_NOT_USABLE",
            "freshness",
            "ERROR",
            "Freshness cannot be assessed from last_updated_on.",
            *dictionary_rule,
        ),
        _definition(
            "INTERPRETABILITY_ALGORITHM_RATES",
            "interpretability",
            "INFO",
            "Algorithm rates were observed and kept separate from dollar rates.",
            *dictionary_rule,
        ),
        _definition(
            "INTERPRETABILITY_NO_PAYER_RATES",
            "interpretability",
            "WARNING",
            "No payer-specific rate objects were observed.",
            *dictionary_rule,
        ),
        _definition(
            "INTERPRETABILITY_PERCENTAGE_RATES",
            "interpretability",
            "INFO",
            "Percentage rates were observed and kept separate from dollar rates.",
            *dictionary_rule,
        ),
        _definition(
            "JSON_ARRAY_ITEM_PROBLEM",
            "conformance",
            "ERROR",
            "One or more charge-array entries could not be decoded as objects.",
            CMS_V3_SCHEMA,
        ),
        _definition(
            "JSON_STREAM_INCOMPLETE",
            "conformance",
            "ERROR",
            "The charge array could not be completely streamed.",
            CMS_V3_SCHEMA,
        ),
        _definition(
            "JSON_UTF8_BOM_PRESENT",
            "conformance",
            "INFO",
            "A UTF-8 byte-order mark was present and tolerated.",
            CMS_JSON_DICTIONARY,
        ),
    ]
    for field_name in _REQUIRED_ENVELOPE_FIELDS:
        definitions.append(
            _definition(
                f"CMS_V3_ENVELOPE_{field_name.upper()}_MISSING",
                "conformance",
                "ERROR",
                f"Required envelope field {field_name} is absent or unusable.",
                *schema_rule,
            )
        )
    for field_name in ("payer_name", "plan_name"):
        definitions.append(
            _definition(
                f"CMS_V3_PAYER_{field_name.upper()}_MISSING",
                "completeness",
                "ERROR",
                f"A payer rate has no usable {field_name}.",
                *schema_rule,
            )
        )
    for field_name in ("10th_percentile", "median_amount", "90th_percentile"):
        definitions.append(
            _definition(
                f"CMS_V3_DERIVED_RATE_{field_name.upper()}_MISSING",
                "completeness",
                "ERROR",
                f"A derived rate has no {field_name} allowed amount.",
                *dictionary_rule,
            )
        )
    for field_name in ("minimum", "maximum"):
        definitions.append(
            _definition(
                f"CMS_V3_DOLLAR_RANGE_{field_name.upper()}_MISSING",
                "completeness",
                "ERROR",
                f"A dollar-rate charge group has no {field_name}.",
                *schema_rule,
            )
        )
    ordered = sorted(definitions, key=lambda definition: definition.code)
    return MappingProxyType({definition.code: definition for definition in ordered})


FINDING_CATALOG: Mapping[str, FindingDefinition] = _build_finding_catalog()

# Bump this value whenever inspection behavior changes without a corresponding catalog or rule-set
# change below. Lakehouse run identity includes INSPECTION_FINGERPRINT so prior findings cannot be
# silently reused after grading semantics change.
INSPECTION_POLICY_VERSION = "cms-hospital-json-v3-inspection-v1"
INSPECTION_FINGERPRINT = hashlib.sha256(
    json.dumps(
        {
            "policy_version": INSPECTION_POLICY_VERSION,
            "array_key": _ARRAY_KEY,
            "required_envelope_fields": _REQUIRED_ENVELOPE_FIELDS,
            "optional_envelope_fields": _OPTIONAL_ENVELOPE_FIELDS,
            "accepted_settings": sorted(ACCEPTED_SETTINGS),
            "accepted_methodologies": sorted(ACCEPTED_METHODOLOGIES),
            "date_pattern": _DATE_RE.pattern,
            "freshness_policy": "strictly-after-calendar-anniversary-v1",
            "finding_catalog": {
                code: {
                    "dimension": definition.dimension,
                    "severity": definition.severity,
                    "description": definition.description,
                    "citations": definition.citations,
                }
                for code, definition in FINDING_CATALOG.items()
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


def explain_finding(code: str) -> FindingDefinition:
    """Return the authoritative definition for ``code`` or raise a clear ``KeyError``."""
    try:
        return FINDING_CATALOG[code]
    except KeyError:
        raise KeyError(f"unknown finding code: {code}") from None


@dataclass(frozen=True)
class DimensionResult:
    """Result for one dimension; status is deliberately not a numerical grade."""

    name: DimensionName
    status: DimensionStatus
    findings: tuple[Finding, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class FileScorecard:
    """Independent file-quality dimensions with no composite rank or compliance label."""

    retrievability: DimensionResult
    conformance: DimensionResult
    completeness: DimensionResult
    interpretability: DimensionResult
    freshness: DimensionResult

    @property
    def dimensions(self) -> tuple[DimensionResult, ...]:
        """Return dimensions in a stable display and serialization order."""
        return (
            self.retrievability,
            self.conformance,
            self.completeness,
            self.interpretability,
            self.freshness,
        )

    @property
    def findings(self) -> tuple[Finding, ...]:
        """Flatten findings without inventing an overall status."""
        return tuple(finding for dimension in self.dimensions for finding in dimension.findings)


@dataclass(frozen=True)
class FileInspection:
    """Small, serializable facts retained after a local file has been streamed."""

    source_path: str
    source_sha256: str
    source_size: int
    publisher: PublisherRef | None
    as_of: date
    envelope: dict[str, object]
    version: str | None
    period: date | None
    item_count: int
    code_count: int
    charge_group_count: int
    payer_rate_count: int
    dollar_rate_count: int
    percentage_rate_count: int
    algorithm_rate_count: int
    settings_seen: tuple[str, ...]
    methodologies_seen: tuple[str, ...]
    missing_envelope_fields: tuple[str, ...]
    had_bom: bool
    scan_completed: bool
    problem_count: int
    problems: tuple[str, ...]
    scorecard: FileScorecard

    @property
    def byte_count(self) -> int:
        """Compatibility name for the exact number of bytes hashed."""
        return self.source_size

    @property
    def last_updated_on(self) -> date | None:
        """The source publication period under its CMS envelope name."""
        return self.period

    @property
    def findings(self) -> tuple[Finding, ...]:
        return self.scorecard.findings

    def to_dict(self) -> dict[str, JSONValue]:
        """Serialize with ISO dates and JSON arrays in deterministic field/key order."""
        converted = _json_value(asdict(self))
        if not isinstance(converted, dict):  # pragma: no cover - guaranteed by dataclass shape
            raise TypeError("FileInspection did not serialize to an object")
        return converted


@dataclass
class _Counts:
    item_count: int = 0
    code_count: int = 0
    charge_group_count: int = 0
    payer_rate_count: int = 0
    dollar_rate_count: int = 0
    percentage_rate_count: int = 0
    algorithm_rate_count: int = 0
    settings_seen: set[str] = field(default_factory=set)
    methodologies_seen: set[str] = field(default_factory=set)


@dataclass
class _FindingDraft:
    code: str
    dimension: DimensionName
    severity: FindingSeverity
    message: str
    citations: tuple[str, ...]
    occurrences: int = 1


class _FindingBook:
    """Deduplicate by finite finding code so malformed files cannot grow the result unbounded."""

    def __init__(self) -> None:
        self._drafts: dict[str, _FindingDraft] = {}

    def add(
        self,
        code: str,
        dimension: DimensionName,
        severity: FindingSeverity,
        message: str,
        citations: tuple[str, ...],
    ) -> None:
        definition = explain_finding(code)
        if (definition.dimension, definition.severity) != (dimension, severity):
            raise ValueError(f"finding {code} does not match its catalog classification")
        prior = self._drafts.get(code)
        if prior is not None:
            prior.occurrences += 1
            return
        self._drafts[code] = _FindingDraft(code, dimension, severity, message, citations)

    def findings(self) -> tuple[Finding, ...]:
        return tuple(
            Finding(
                code=draft.code,
                dimension=draft.dimension,
                severity=draft.severity,
                message=draft.message,
                citations=draft.citations,
                occurrences=draft.occurrences,
            )
            for draft in sorted(self._drafts.values(), key=lambda item: item.code)
        )


class _CappedProblems(list[str]):
    """A list accepted by StreamStats that counts everything but retains only small samples."""

    def __init__(self) -> None:
        super().__init__()
        self.total_count = 0

    def append(self, problem: str) -> None:
        self.total_count += 1
        if len(self) < _PROBLEM_SAMPLE_LIMIT:
            super().append(problem[:_PROBLEM_TEXT_LIMIT])


def _json_value(value: object) -> JSONValue:
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        pairs = sorted(((str(key), item) for key, item in value.items()), key=lambda pair: pair[0])
        return {key: _json_value(item) for key, item in pairs}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported inspection value: {type(value).__name__}")


def _hash_source(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(_READ_CHUNK):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_text_array(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(_is_text(item) for item in value)


def _is_license(value: object) -> bool:
    return isinstance(value, dict) and _is_text(value.get("state"))


def _is_attestation(value: object) -> bool:
    return (
        isinstance(value, dict)
        and _is_text(value.get("attestation"))
        and isinstance(value.get("confirm_attestation"), bool)
        and _is_text(value.get("attester_name"))
    )


def _valid_envelope_value(field_name: str, value: object) -> bool:
    if field_name in {"hospital_name", "last_updated_on", "version"}:
        return _is_text(value)
    if field_name in {"location_name", "hospital_address", "type_2_npi"}:
        return _is_text_array(value)
    if field_name == "license_information":
        return _is_license(value)
    return _is_attestation(value)


def _inspect_envelope(
    envelope: Mapping[str, object], book: _FindingBook
) -> tuple[tuple[str, ...], str | None, date | None]:
    missing: list[str] = []
    for field_name in _REQUIRED_ENVELOPE_FIELDS:
        if _valid_envelope_value(field_name, envelope.get(field_name)):
            continue
        missing.append(field_name)
        book.add(
            f"CMS_V3_ENVELOPE_{field_name.upper()}_MISSING",
            "conformance",
            "ERROR",
            f"Required CMS v3 envelope field {field_name!r} is absent or unusable.",
            (CMS_V3_SCHEMA, CFR_180_50),
        )

    version_value = envelope.get("version")
    version = version_value if isinstance(version_value, str) and version_value else None
    if version is not None and version != "3.0.0":
        book.add(
            "CMS_V3_VERSION_UNEXPECTED",
            "conformance",
            "ERROR",
            f"Template version is {version!r}; this inspector implements CMS JSON v3.0.0.",
            (CMS_V3_SCHEMA, CMS_JSON_DICTIONARY),
        )

    period = _parse_period(envelope.get("last_updated_on"), book)
    attestation = envelope.get("attestation")
    if isinstance(attestation, dict) and attestation.get("confirm_attestation") is False:
        book.add(
            "CMS_V3_ATTESTATION_NOT_CONFIRMED",
            "conformance",
            "WARNING",
            "The required attestation object explicitly has confirm_attestation=false.",
            (CMS_JSON_DICTIONARY, CFR_180_50),
        )
    return tuple(missing), version, period


def _parse_period(value: object, book: _FindingBook) -> date | None:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        if value is not None:
            book.add(
                "CMS_V3_LAST_UPDATED_ON_INVALID",
                "conformance",
                "ERROR",
                "The last_updated_on value is not an ISO YYYY-MM-DD date.",
                (CMS_V3_SCHEMA, CFR_180_50),
            )
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        book.add(
            "CMS_V3_LAST_UPDATED_ON_INVALID",
            "conformance",
            "ERROR",
            "The last_updated_on value is not a real calendar date.",
            (CMS_V3_SCHEMA, CFR_180_50),
        )
        return None


def _inspect_item(item: dict[str, object], counts: _Counts, book: _FindingBook) -> None:
    counts.item_count += 1
    if not _is_text(item.get("description")):
        book.add(
            "CMS_V3_ITEM_DESCRIPTION_MISSING",
            "completeness",
            "ERROR",
            "A standard charge information object lacks a usable description.",
            (CMS_V3_SCHEMA, CFR_180_50),
        )
    _inspect_codes(item.get("code_information"), counts, book)
    _inspect_charge_groups(item.get("standard_charges"), counts, book)


def _inspect_codes(value: object, counts: _Counts, book: _FindingBook) -> None:
    if not isinstance(value, list) or not value:
        book.add(
            "CMS_V3_ITEM_CODE_INFORMATION_MISSING",
            "completeness",
            "ERROR",
            "A standard charge information object lacks a non-empty code_information array.",
            (CMS_V3_SCHEMA, CFR_180_50),
        )
        return
    counts.code_count += len(value)
    for code in value:
        if not isinstance(code, dict) or not _is_text(code.get("code")):
            book.add(
                "CMS_V3_CODE_VALUE_MISSING",
                "completeness",
                "ERROR",
                "A code_information entry lacks a usable code value.",
                (CMS_V3_SCHEMA, CFR_180_50),
            )
        if not isinstance(code, dict) or not _is_text(code.get("type")):
            book.add(
                "CMS_V3_CODE_TYPE_MISSING",
                "completeness",
                "ERROR",
                "A code_information entry lacks a usable code type.",
                (CMS_V3_SCHEMA, CFR_180_50),
            )


def _inspect_charge_groups(value: object, counts: _Counts, book: _FindingBook) -> None:
    if not isinstance(value, list) or not value:
        book.add(
            "CMS_V3_ITEM_STANDARD_CHARGES_MISSING",
            "completeness",
            "ERROR",
            "A standard charge information object lacks a non-empty standard_charges array.",
            (CMS_V3_SCHEMA, CFR_180_50),
        )
        return
    counts.charge_group_count += len(value)
    for charge in value:
        if not isinstance(charge, dict):
            book.add(
                "CMS_V3_CHARGE_GROUP_NOT_OBJECT",
                "conformance",
                "ERROR",
                "A standard_charges entry is not a JSON object.",
                (CMS_V3_SCHEMA,),
            )
            continue
        _inspect_charge_group(charge, counts, book)


def _inspect_charge_group(
    charge: dict[object, object], counts: _Counts, book: _FindingBook
) -> None:
    setting = charge.get("setting")
    if isinstance(setting, str) and setting in ACCEPTED_SETTINGS:
        counts.settings_seen.add(setting)
    else:
        book.add(
            "CMS_V3_SETTING_INVALID",
            "conformance",
            "ERROR",
            f"A charge setting is not one of {sorted(ACCEPTED_SETTINGS)!r}; first value: "
            f"{_short_value(setting)}.",
            (CMS_JSON_DICTIONARY, CFR_180_50),
        )

    payer_value = charge.get("payers_information")
    has_base_charge = "gross_charge" in charge or "discounted_cash" in charge
    if not has_base_charge and not (isinstance(payer_value, list) and payer_value):
        book.add(
            "CMS_V3_CHARGE_VALUE_MISSING",
            "completeness",
            "ERROR",
            "A charge group has no gross, discounted-cash, or payer-specific charge.",
            (CMS_V3_SCHEMA, CFR_180_50),
        )
    if payer_value is not None:
        _inspect_payers(payer_value, charge, counts, book)


def _inspect_payers(
    value: object,
    charge: Mapping[object, object],
    counts: _Counts,
    book: _FindingBook,
) -> None:
    if not isinstance(value, list) or not value:
        book.add(
            "CMS_V3_PAYERS_INFORMATION_INVALID",
            "conformance",
            "ERROR",
            "A present payers_information value is not a non-empty array.",
            (CMS_V3_SCHEMA,),
        )
        return
    counts.payer_rate_count += len(value)
    has_dollar = False
    for payer in value:
        if not isinstance(payer, dict):
            book.add(
                "CMS_V3_PAYER_RATE_NOT_OBJECT",
                "conformance",
                "ERROR",
                "A payers_information entry is not a JSON object.",
                (CMS_V3_SCHEMA,),
            )
            continue
        if "standard_charge_dollar" in payer:
            has_dollar = True
        _inspect_payer(payer, counts, book)
    if has_dollar:
        _require_dollar_range(charge, book)


def _inspect_payer(payer: dict[object, object], counts: _Counts, book: _FindingBook) -> None:
    for field_name in ("payer_name", "plan_name"):
        if not _is_text(payer.get(field_name)):
            book.add(
                f"CMS_V3_PAYER_{field_name.upper()}_MISSING",
                "completeness",
                "ERROR",
                f"A payer rate lacks a usable {field_name}.",
                (CMS_V3_SCHEMA, CFR_180_50),
            )
    _inspect_methodology(payer, counts, book)
    kinds = _count_rate_kinds(payer, counts)
    if kinds == 0:
        book.add(
            "CMS_V3_PAYER_CHARGE_MISSING",
            "completeness",
            "ERROR",
            "A payer rate encodes no dollar, percentage, or algorithm charge.",
            (CMS_V3_SCHEMA, CFR_180_50),
        )
    if "standard_charge_percentage" in payer or "standard_charge_algorithm" in payer:
        _inspect_derived_rate_fields(payer, book)


def _inspect_methodology(
    payer: Mapping[object, object], counts: _Counts, book: _FindingBook
) -> None:
    methodology = payer.get("methodology")
    if isinstance(methodology, str) and methodology in ACCEPTED_METHODOLOGIES:
        counts.methodologies_seen.add(methodology)
        if methodology == "other" and not _is_text(payer.get("additional_payer_notes")):
            book.add(
                "CMS_V3_OTHER_METHODOLOGY_NOTES_MISSING",
                "completeness",
                "ERROR",
                "A methodology of 'other' lacks required additional_payer_notes.",
                (CMS_JSON_DICTIONARY, CMS_V3_SCHEMA),
            )
        return
    book.add(
        "CMS_V3_METHODOLOGY_INVALID",
        "conformance",
        "ERROR",
        f"A payer methodology is not in the CMS v3 accepted set; first value: "
        f"{_short_value(methodology)}.",
        (CMS_JSON_DICTIONARY, CFR_180_50),
    )


def _count_rate_kinds(payer: Mapping[object, object], counts: _Counts) -> int:
    present = 0
    if "standard_charge_dollar" in payer:
        counts.dollar_rate_count += 1
        present += 1
    if "standard_charge_percentage" in payer:
        counts.percentage_rate_count += 1
        present += 1
    if "standard_charge_algorithm" in payer:
        counts.algorithm_rate_count += 1
        present += 1
    return present


def _inspect_derived_rate_fields(payer: Mapping[object, object], book: _FindingBook) -> None:
    count_value = payer.get("count")
    if not _is_text(count_value):
        book.add(
            "CMS_V3_DERIVED_RATE_COUNT_MISSING",
            "completeness",
            "ERROR",
            "A percentage or algorithm charge lacks the required allowed-amount count.",
            (CMS_JSON_DICTIONARY, CFR_180_50),
        )
    if count_value == "0":
        if not _is_text(payer.get("additional_payer_notes")):
            book.add(
                "CMS_V3_ZERO_COUNT_NOTES_MISSING",
                "completeness",
                "ERROR",
                "A derived rate with count '0' lacks required additional_payer_notes.",
                (CMS_JSON_DICTIONARY, CMS_V3_SCHEMA),
            )
        return
    for field_name in ("10th_percentile", "median_amount", "90th_percentile"):
        if field_name not in payer:
            book.add(
                f"CMS_V3_DERIVED_RATE_{field_name.upper()}_MISSING",
                "completeness",
                "ERROR",
                f"A percentage or algorithm charge lacks {field_name}.",
                (CMS_JSON_DICTIONARY, CFR_180_50),
            )


def _require_dollar_range(charge: Mapping[object, object], book: _FindingBook) -> None:
    for field_name in ("minimum", "maximum"):
        if field_name not in charge:
            book.add(
                f"CMS_V3_DOLLAR_RANGE_{field_name.upper()}_MISSING",
                "completeness",
                "ERROR",
                f"A charge group with a dollar payer rate lacks {field_name}.",
                (CMS_V3_SCHEMA, CFR_180_50),
            )


def _short_value(value: object) -> str:
    return repr(value)[:80]


def _add_stream_findings(
    book: _FindingBook,
    problems: _CappedProblems,
    stream_error: str | None,
    had_bom: bool,
) -> None:
    if had_bom:
        book.add(
            "JSON_UTF8_BOM_PRESENT",
            "conformance",
            "INFO",
            "The source begins with a UTF-8 byte-order mark; it was tolerated and recorded.",
            (CMS_JSON_DICTIONARY,),
        )
    nonfatal_count = problems.total_count - (1 if stream_error is not None else 0)
    if nonfatal_count > 0:
        book.add(
            "JSON_ARRAY_ITEM_PROBLEM",
            "conformance",
            "ERROR",
            "One or more standard-charge array entries could not be decoded as JSON objects.",
            (CMS_V3_SCHEMA,),
        )
    if stream_error is not None:
        book.add(
            "JSON_STREAM_INCOMPLETE",
            "conformance",
            "ERROR",
            f"The standard-charge array could not be completely streamed: {stream_error[:160]}",
            (CMS_V3_SCHEMA,),
        )


def _one_year_after(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:  # February 29 has no anniversary in a non-leap year.
        return value.replace(year=value.year + 1, day=28)


def _add_freshness_finding(period: date | None, as_of: date, book: _FindingBook) -> None:
    if period is None:
        book.add(
            "FRESHNESS_DATE_NOT_USABLE",
            "freshness",
            "ERROR",
            "Freshness cannot be assessed because last_updated_on is absent or invalid.",
            (CMS_JSON_DICTIONARY, CFR_180_50),
        )
    elif period > as_of:
        book.add(
            "FRESHNESS_DATE_IN_FUTURE",
            "freshness",
            "WARNING",
            f"The source update date {period.isoformat()} is after as_of {as_of.isoformat()}.",
            (CFR_180_50,),
        )
    elif as_of > _one_year_after(period):
        book.add(
            "FRESHNESS_ANNUAL_UPDATE_OVERDUE",
            "freshness",
            "WARNING",
            f"The source date {period.isoformat()} is more than one year before "
            f"as_of {as_of.isoformat()}.",
            (CFR_180_50,),
        )


def _add_interpretability_findings(counts: _Counts, book: _FindingBook) -> None:
    if counts.payer_rate_count == 0:
        book.add(
            "INTERPRETABILITY_NO_PAYER_RATES",
            "interpretability",
            "WARNING",
            "No payer-specific rate objects were observed in the streamed charge rows.",
            (CMS_JSON_DICTIONARY, CFR_180_50),
        )
    if counts.percentage_rate_count:
        book.add(
            "INTERPRETABILITY_PERCENTAGE_RATES",
            "interpretability",
            "INFO",
            "Percentage-encoded payer rates are not directly dollar-denominated and are counted "
            "separately.",
            (CMS_JSON_DICTIONARY, CFR_180_50),
        )
    if counts.algorithm_rate_count:
        book.add(
            "INTERPRETABILITY_ALGORITHM_RATES",
            "interpretability",
            "INFO",
            "Algorithm-encoded payer rates are not directly dollar-denominated and are counted "
            "separately.",
            (CMS_JSON_DICTIONARY, CFR_180_50),
        )


def _dimension(
    name: DimensionName,
    findings: tuple[Finding, ...],
    *,
    status: DimensionStatus | None = None,
    note: str | None = None,
) -> DimensionResult:
    selected = tuple(finding for finding in findings if finding.dimension == name)
    resolved: DimensionStatus = status or ("FINDINGS" if selected else "OBSERVED")
    return DimensionResult(name=name, status=resolved, findings=selected, note=note)


def _scorecard(
    findings: tuple[Finding, ...], *, scan_completed: bool, item_count: int
) -> FileScorecard:
    completeness_status: DimensionStatus | None = None
    interpretability_status: DimensionStatus | None = None
    if not scan_completed:
        completeness_status = "NOT_ASSESSED"
        interpretability_status = "NOT_ASSESSED"
    elif item_count == 0:
        interpretability_status = "NOT_ASSESSED"
    return FileScorecard(
        retrievability=_dimension(
            "retrievability",
            findings,
            status="NOT_ASSESSED",
            note="Local-file inspection does not perform or infer a network retrieval.",
        ),
        conformance=_dimension("conformance", findings),
        completeness=_dimension("completeness", findings, status=completeness_status),
        interpretability=_dimension("interpretability", findings, status=interpretability_status),
        freshness=_dimension("freshness", findings),
    )


def inspect_hospital_file(
    path: str | Path,
    publisher: PublisherRef | None = None,
    *,
    as_of: date,
) -> FileInspection:
    """Inspect one local CMS hospital JSON v3 file with bounded memory.

    ``as_of`` is required so freshness is repeatable rather than dependent on the wall clock.
    Filesystem errors are allowed to propagate: retrievability means remote publication access and
    is explicitly ``NOT_ASSESSED`` by this local-only function.
    """
    source_path = Path(path)
    source_sha256, source_size = _hash_source(source_path)
    book = _FindingBook()
    envelope: dict[str, object] = {}
    counts = _Counts()
    problems = _CappedProblems()
    stats = StreamStats(problems=problems)
    stream_error: str | None = None
    with source_path.open("rb") as source:
        try:
            for item in stream_array_items(
                source,
                _ARRAY_KEY,
                stats=stats,
                envelope=envelope,
                envelope_keys=(*_REQUIRED_ENVELOPE_FIELDS, *_OPTIONAL_ENVELOPE_FIELDS),
            ):
                _inspect_item(item, counts, book)
        except StreamError as exc:
            stream_error = str(exc)
            problems.append(f"stream error: {stream_error}")

    missing_fields, version, period = _inspect_envelope(envelope, book)
    scan_completed = stream_error is None
    missing = list(missing_fields)
    if stream_error is not None and (
        f"no '{_ARRAY_KEY}' array" in stream_error
        or f"'{_ARRAY_KEY}' is not an array" in stream_error
    ):
        missing.append(_ARRAY_KEY)
        book.add(
            "CMS_V3_ENVELOPE_STANDARD_CHARGE_INFORMATION_MISSING",
            "conformance",
            "ERROR",
            "Required CMS v3 standard_charge_information array is absent or unusable.",
            (CMS_V3_SCHEMA, CFR_180_50),
        )
    if scan_completed and counts.item_count == 0:
        book.add(
            "CMS_V3_STANDARD_CHARGE_INFORMATION_EMPTY",
            "completeness",
            "ERROR",
            "The standard_charge_information array contains no usable item objects.",
            (CMS_V3_SCHEMA, CFR_180_50),
        )

    _add_stream_findings(book, problems, stream_error, stats.had_bom)
    _add_freshness_finding(period, as_of, book)
    _add_interpretability_findings(counts, book)
    findings = book.findings()
    scorecard = _scorecard(findings, scan_completed=scan_completed, item_count=counts.item_count)
    return FileInspection(
        source_path=str(source_path),
        source_sha256=source_sha256,
        source_size=source_size,
        publisher=publisher,
        as_of=as_of,
        envelope=envelope,
        version=version,
        period=period,
        item_count=counts.item_count,
        code_count=counts.code_count,
        charge_group_count=counts.charge_group_count,
        payer_rate_count=counts.payer_rate_count,
        dollar_rate_count=counts.dollar_rate_count,
        percentage_rate_count=counts.percentage_rate_count,
        algorithm_rate_count=counts.algorithm_rate_count,
        settings_seen=tuple(sorted(counts.settings_seen)),
        methodologies_seen=tuple(sorted(counts.methodologies_seen)),
        missing_envelope_fields=tuple(missing),
        had_bom=stats.had_bom,
        scan_completed=scan_completed,
        problem_count=problems.total_count,
        problems=tuple(problems),
        scorecard=scorecard,
    )
