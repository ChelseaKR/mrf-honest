"""Bounded, deterministic inspection of local CMS hospital CSV v3 files, Tall and Wide.

This module reports observable file properties for the CSV template of the same regulation the
JSON inspector covers.  It does not run CMS's validator, make a legal compliance determination,
test a remote URL, or collapse unlike dimensions into a numerical rank.  The selected checks are
grounded in CMS's v3.0 CSV data dictionary, the published v3.0.0 Tall and Wide templates, and
45 CFR 180.50; every finding carries the applicable primary-source link.

The table is streamed row by row and only counters, bounded problem samples, and one finding per
code are retained.  Peak memory therefore follows the widest single row plus one bounded field,
rather than the size of the source file.

Two tolerances the dictionary states are implemented as tolerances rather than findings: header
matching is case-insensitive and ignores spaces around pipes ("inadvertently inserting spaces
will not generate a deficiency"), and headers are matched by name rather than position, because
the published real-world files order the general elements differently from the template while
carrying exactly the required set.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType

from mrf_honest.inspect import (
    ACCEPTED_METHODOLOGIES,
    ACCEPTED_SETTINGS,
    CFR_180_50,
    DimensionName,
    DimensionResult,
    DimensionStatus,
    FileScorecard,
    Finding,
    FindingDefinition,
    FindingSeverity,
    JSONValue,
)
from mrf_honest.types import PublisherRef

CMS_CSV_DICTIONARY = (
    "https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/CSV/README.md"
)
CMS_CSV_TALL_TEMPLATE = (
    "https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/CSV/"
    "templates/V3.0.0_Tall_CSV_Format_Template.csv"
)
CMS_CSV_WIDE_TEMPLATE = (
    "https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/CSV/"
    "templates/V3.0.0_Wide_CSV_Format_Template.csv"
)

#: Valid values from the dictionary's "Additional Notes for Drug Type Of Measurement Values".
ACCEPTED_DRUG_TYPES = frozenset({"gr", "me", "ml", "un", "f2", "ea", "gm"})

#: Valid values from the dictionary's "Additional Notes Concerning Code Types".
ACCEPTED_CODE_TYPES = frozenset(
    {
        "cpt",
        "ndc",
        "hcpcs",
        "rc",
        "icd",
        "drg",
        "ms-drg",
        "r-drg",
        "s-drg",
        "aps-drg",
        "ap-drg",
        "apr-drg",
        "apc",
        "local",
        "eapg",
        "hipps",
        "cdt",
        "cdm",
        "tris-drg",
        "cmg",
        "ms-ltc-drg",
    }
)

#: The dictionary requires this exact text as the attestation column header.
ATTESTATION_HEADER_TEXT = (
    "To the best of its knowledge and belief, this hospital has included all applicable standard "
    "charge information in accordance with the requirements of 45 CFR 180.50, and the information "
    "encoded is true, accurate, and complete as of the date in the file. This hospital has "
    "included all payer-specific negotiated charges in dollars that can be expressed as a dollar "
    "amount. For payer-specific negotiated charges that cannot be expressed as a dollar amount in "
    "the machine-readable file or not knowable in advance, the hospital attests that the "
    "payer-specific negotiated charge is based on a contractual algorithm, percentage or formula "
    "that precludes the provision of a dollar amount and has provided all necessary information "
    "available to the hospital for the public to be able to derive the dollar amount, including, "
    "but not limited to, the specific fee schedule or components referenced in such percentage, "
    "algorithm or formula."
)

_REQUIRED_GENERAL_FIELDS = (
    "hospital_name",
    "last_updated_on",
    "version",
    "location_name",
    "hospital_address",
    "type_2_npi",
    "license_number",
    "attester_name",
    "attestation",
)
_SHARED_REQUIRED_COLUMNS = (
    "description",
    "setting",
    "drug_unit_of_measurement",
    "drug_type_of_measurement",
    "standard_charge|gross",
    "standard_charge|discounted_cash",
    "modifiers",
    "standard_charge|min",
    "standard_charge|max",
    "additional_generic_notes",
)
_TALL_REQUIRED_COLUMNS = (
    "payer_name",
    "plan_name",
    "standard_charge|negotiated_dollar",
    "standard_charge|negotiated_percentage",
    "standard_charge|negotiated_algorithm",
    "median_amount",
    "10th_percentile",
    "90th_percentile",
    "count",
    "standard_charge|methodology",
)
#: The dictionary's "Additional CSV Placeholder Notes": once one payer-and-plan combination
#: appears in any of these nine Wide headers, all nine are required for that combination.
_WIDE_COMBO_ELEMENTS = (
    "standard_charge|negotiated_dollar",
    "standard_charge|negotiated_percentage",
    "standard_charge|negotiated_algorithm",
    "standard_charge|methodology",
    "median_amount",
    "10th_percentile",
    "90th_percentile",
    "count",
    "additional_payer_notes",
)
_NUMERIC_COLUMNS = (
    "drug_unit_of_measurement",
    "standard_charge|gross",
    "standard_charge|discounted_cash",
    "standard_charge|negotiated_dollar",
    "standard_charge|negotiated_percentage",
    "standard_charge|min",
    "standard_charge|max",
    "median_amount",
    "10th_percentile",
    "90th_percentile",
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_STATE_RE = re.compile(r"^[a-z]{2}$")
_COUNT_INTEGER_RE = re.compile(r"^(0|[1-9]\d*)$")
_PROBLEM_SAMPLE_LIMIT = 20
_PROBLEM_TEXT_LIMIT = 300
_VALUE_TEXT_LIMIT = 300
_READ_CHUNK = 1 << 20
#: One bounded field; algorithm text runs long, but a field this large is a structural problem.
_FIELD_SIZE_LIMIT = 10 * 1024 * 1024


def _definition(
    code: str,
    dimension: DimensionName,
    severity: FindingSeverity,
    description: str,
    *citations: str,
) -> FindingDefinition:
    return FindingDefinition(code, dimension, severity, description, citations)


def _build_finding_catalog() -> Mapping[str, FindingDefinition]:
    dictionary_rule = (CMS_CSV_DICTIONARY, CFR_180_50)
    template_rule = (CMS_CSV_TALL_TEMPLATE, CMS_CSV_WIDE_TEMPLATE, CMS_CSV_DICTIONARY)
    definitions = [
        _definition(
            "CMS_CSV_ATTESTATION_NOT_CONFIRMED",
            "conformance",
            "WARNING",
            "The attestation value is explicitly false.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_CHARGE_HEADER_ROW_MISSING",
            "conformance",
            "ERROR",
            "The file has no row 3 of standard-charge column headers.",
            *template_rule,
        ),
        _definition(
            "CMS_CSV_CHARGE_VALUE_MISSING",
            "completeness",
            "ERROR",
            "An encoded item or service row carries no gross, cash, or payer-specific charge.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_CODE_PAIRING_MISSING",
            "completeness",
            "ERROR",
            "A row with a standard charge has no complete code and code-type pairing.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_CODE_TYPE_INVALID",
            "conformance",
            "ERROR",
            "A code-type value is outside the CMS accepted set.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_CODE_TYPE_UNPAIRED",
            "completeness",
            "ERROR",
            "A code without its code type, or a code type without its code.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_COUNT_VALUE_INVALID",
            "conformance",
            "ERROR",
            "A count of allowed amounts is not '0', '1 through 10', or a whole number over ten.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_DERIVED_RATE_COUNT_MISSING",
            "completeness",
            "ERROR",
            "A percentage or algorithm rate has no allowed-amount count.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_DERIVED_RATE_PERCENTILES_MISSING",
            "completeness",
            "ERROR",
            "A percentage or algorithm rate with a nonzero count lacks its allowed-amount "
            "median, 10th percentile, or 90th percentile.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_DESCRIPTION_MISSING",
            "completeness",
            "ERROR",
            "A data row with codes or charges has no usable description.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_DOLLAR_RANGE_MISSING",
            "completeness",
            "ERROR",
            "A row with a dollar-denominated payer rate lacks the de-identified minimum or "
            "maximum negotiated charge.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_DRUG_FIELDS_UNPAIRED",
            "completeness",
            "ERROR",
            "A drug unit of measurement without its type of measurement, or the reverse.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_DRUG_TYPE_INVALID",
            "conformance",
            "ERROR",
            "A drug type of measurement is outside the CMS accepted set.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_ENCODING_NOT_UTF8",
            "conformance",
            "INFO",
            "The file is not valid UTF-8; it was read as Latin-1 and recorded.",
            (CMS_CSV_DICTIONARY),
        ),
        _definition(
            "CMS_CSV_HEADER_NOT_UNIQUE",
            "conformance",
            "ERROR",
            "A column header appears more than once across rows 1 and 3.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_LAST_UPDATED_ON_INVALID",
            "conformance",
            "ERROR",
            "last_updated_on is not an ISO YYYY-MM-DD or M/D/YYYY date.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_LAYOUT_AMBIGUOUS",
            "conformance",
            "ERROR",
            "The charge header row mixes Tall payer columns with Wide payer-specific headers.",
            *template_rule,
        ),
        _definition(
            "CMS_CSV_METHODOLOGY_INVALID",
            "conformance",
            "ERROR",
            "A standard-charge methodology is outside the CMS accepted set.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_MODIFIER_ROW_CONTEXT_MISSING",
            "completeness",
            "ERROR",
            "A modifier row without an item or service lacks the required description or "
            "accompanying charge or note.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_NDC_DRUG_FIELDS_MISSING",
            "completeness",
            "ERROR",
            "An NDC-coded row lacks its drug unit or type of measurement.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_NUMERIC_VALUE_INVALID",
            "conformance",
            "ERROR",
            "A numeric data element holds something other than a positive number.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_OTHER_METHODOLOGY_NOTES_MISSING",
            "completeness",
            "ERROR",
            "An 'other' methodology has no explanatory note.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_PAYER_CONTEXT_MISSING",
            "completeness",
            "ERROR",
            "A payer-specific charge without its payer name, plan name, or methodology.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_PAYER_WITHOUT_CHARGE",
            "completeness",
            "ERROR",
            "A payer or plan name is encoded with no payer-specific charge beside it.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_PLACEHOLDER_NOT_REPLACED",
            "conformance",
            "ERROR",
            "A template placeholder such as [state], [i], [payer_name], or [plan_name] was "
            "published without being replaced.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_ROW_WIDTH_MISMATCH",
            "conformance",
            "ERROR",
            "A data row carries non-blank cells beyond the declared columns.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_SETTING_INVALID",
            "conformance",
            "ERROR",
            "A setting value is outside the CMS accepted set, or blank where blanks are not "
            "accepted.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_STREAM_INCOMPLETE",
            "conformance",
            "ERROR",
            "The CSV table could not be completely streamed.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_TABLE_EMPTY",
            "completeness",
            "ERROR",
            "The table contains no usable data rows after the header rows.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_UTF8_BOM_PRESENT",
            "conformance",
            "INFO",
            "A UTF-8 byte-order mark was present and tolerated.",
            (CMS_CSV_DICTIONARY),
        ),
        _definition(
            "CMS_CSV_VERSION_UNEXPECTED",
            "conformance",
            "ERROR",
            "The template version is not the v3.0.0 version implemented here.",
            *template_rule,
        ),
        _definition(
            "CMS_CSV_WIDE_PAYER_HEADER_SET_INCOMPLETE",
            "conformance",
            "ERROR",
            "A Wide payer-and-plan combination is missing some of its nine required headers.",
            *dictionary_rule,
        ),
        _definition(
            "CMS_CSV_ZERO_COUNT_NOTES_MISSING",
            "completeness",
            "ERROR",
            "A derived rate with count zero has no explanatory note.",
            *dictionary_rule,
        ),
        _definition(
            "CSV_FRESHNESS_DATE_NOT_USABLE",
            "freshness",
            "ERROR",
            "Freshness cannot be assessed from last_updated_on.",
            *dictionary_rule,
        ),
        # The two calendar findings are deliberately identical to the JSON catalog's: the
        # freshness rule is the regulation's, not a template's, and one code must mean one thing.
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
            "CSV_INTERPRETABILITY_ALGORITHM_RATES",
            "interpretability",
            "INFO",
            "Algorithm rates were observed and kept separate from dollar rates.",
            *dictionary_rule,
        ),
        _definition(
            "CSV_INTERPRETABILITY_NO_PAYER_RATES",
            "interpretability",
            "WARNING",
            "No payer-specific rate values were observed.",
            *dictionary_rule,
        ),
        _definition(
            "CSV_INTERPRETABILITY_PERCENTAGE_RATES",
            "interpretability",
            "INFO",
            "Percentage rates were observed and kept separate from dollar rates.",
            *dictionary_rule,
        ),
    ]
    for field_name in _REQUIRED_GENERAL_FIELDS:
        definitions.append(
            _definition(
                f"CMS_CSV_GENERAL_{field_name.upper()}_MISSING",
                "conformance",
                "ERROR",
                f"Required general data element {field_name} is absent or unusable.",
                *dictionary_rule,
            )
        )
    for column in (*_SHARED_REQUIRED_COLUMNS, *_TALL_REQUIRED_COLUMNS):
        definitions.append(
            _definition(
                f"CMS_CSV_COLUMN_{_column_code(column)}_MISSING",
                "conformance",
                "ERROR",
                f"Required standard-charge column {column} is absent from the header row.",
                *dictionary_rule,
            )
        )
    definitions.append(
        _definition(
            "CMS_CSV_COLUMN_CODE_PAIR_MISSING",
            "conformance",
            "ERROR",
            "No code|1 and code|1|type column pair is present in the header row.",
            *dictionary_rule,
        )
    )
    ordered = sorted(definitions, key=lambda definition: definition.code)
    return MappingProxyType({definition.code: definition for definition in ordered})


def _column_code(column: str) -> str:
    return column.replace("|", "_").upper()


CSV_FINDING_CATALOG: Mapping[str, FindingDefinition] = _build_finding_catalog()

# Bump this value whenever inspection behavior changes without a corresponding catalog or rule-set
# change below. The scorecard assessment fingerprint includes CSV_INSPECTION_FINGERPRINT so prior
# findings cannot be silently reused after grading semantics change.
CSV_INSPECTION_POLICY_VERSION = "cms-hospital-csv-v3-inspection-v1"
CSV_INSPECTION_FINGERPRINT = hashlib.sha256(
    json.dumps(
        {
            "policy_version": CSV_INSPECTION_POLICY_VERSION,
            "required_general_fields": _REQUIRED_GENERAL_FIELDS,
            "shared_required_columns": _SHARED_REQUIRED_COLUMNS,
            "tall_required_columns": _TALL_REQUIRED_COLUMNS,
            "wide_combo_elements": _WIDE_COMBO_ELEMENTS,
            "numeric_columns": _NUMERIC_COLUMNS,
            "accepted_settings": sorted(ACCEPTED_SETTINGS),
            "accepted_methodologies": sorted(ACCEPTED_METHODOLOGIES),
            "accepted_drug_types": sorted(ACCEPTED_DRUG_TYPES),
            "accepted_code_types": sorted(ACCEPTED_CODE_TYPES),
            "attestation_header_sha256": hashlib.sha256(
                ATTESTATION_HEADER_TEXT.encode()
            ).hexdigest(),
            "date_rule": "iso-or-slash-dates-v1",
            "header_rule": "case-insensitive-pipe-trimmed-order-independent-v1",
            "layout_rule": "wide-iff-parameterized-headers-v1",
            "row_width_rule": "short-rows-read-as-blanks-extra-nonblank-cells-flagged-v1",
            "encoding_rule": "utf8-then-latin1-with-info-v1",
            "field_size_limit": _FIELD_SIZE_LIMIT,
            "freshness_policy": "strictly-after-calendar-anniversary-v1",
            "finding_catalog": {
                code: {
                    "dimension": definition.dimension,
                    "severity": definition.severity,
                    "description": definition.description,
                    "citations": definition.citations,
                }
                for code, definition in CSV_FINDING_CATALOG.items()
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


def explain_csv_finding(code: str) -> FindingDefinition:
    """Return the authoritative definition for ``code`` or raise a clear ``KeyError``."""
    try:
        return CSV_FINDING_CATALOG[code]
    except KeyError:
        raise KeyError(f"unknown finding code: {code}") from None


@dataclass(frozen=True)
class CsvFileInspection:
    """Small, serializable facts retained after a local CSV file has been streamed."""

    source_path: str
    source_sha256: str
    source_size: int
    publisher: PublisherRef | None
    as_of: date
    envelope: dict[str, object]
    version: str | None
    period: date | None
    layout: str | None
    row_count: int
    item_count: int
    code_count: int
    payer_plan_combination_count: int
    payer_rate_count: int
    dollar_rate_count: int
    percentage_rate_count: int
    algorithm_rate_count: int
    settings_seen: tuple[str, ...]
    methodologies_seen: tuple[str, ...]
    missing_general_fields: tuple[str, ...]
    missing_columns: tuple[str, ...]
    had_bom: bool
    encoding: str
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
        """The source publication period under its CMS general-element name."""
        return self.period

    @property
    def findings(self) -> tuple[Finding, ...]:
        return self.scorecard.findings

    def to_dict(self) -> dict[str, JSONValue]:
        """Serialize with ISO dates and JSON arrays in deterministic field/key order."""
        converted = _json_value(asdict(self))
        if not isinstance(converted, dict):  # pragma: no cover - guaranteed by dataclass shape
            raise TypeError("CsvFileInspection did not serialize to an object")
        return converted


@dataclass
class _Counts:
    row_count: int = 0
    item_count: int = 0
    code_count: int = 0
    payer_rate_count: int = 0
    dollar_rate_count: int = 0
    percentage_rate_count: int = 0
    algorithm_rate_count: int = 0
    settings_seen: set[str] = field(default_factory=set)
    methodologies_seen: set[str] = field(default_factory=set)


@dataclass
class _FindingDraft:
    code: str
    message: str
    occurrences: int = 1


class _FindingBook:
    """Deduplicate by finite finding code so malformed files cannot grow the result unbounded."""

    def __init__(self) -> None:
        self._drafts: dict[str, _FindingDraft] = {}

    def add(self, code: str, message: str) -> None:
        explain_csv_finding(code)  # unknown codes are programmer errors and raise here
        prior = self._drafts.get(code)
        if prior is not None:
            prior.occurrences += 1
            return
        self._drafts[code] = _FindingDraft(code, message)

    def has(self, code: str) -> bool:
        return code in self._drafts

    def findings(self) -> tuple[Finding, ...]:
        results = []
        for draft in sorted(self._drafts.values(), key=lambda item: item.code):
            definition = explain_csv_finding(draft.code)
            results.append(
                Finding(
                    code=draft.code,
                    dimension=definition.dimension,
                    severity=definition.severity,
                    message=draft.message,
                    citations=definition.citations,
                    occurrences=draft.occurrences,
                )
            )
        return tuple(results)


class _CappedProblems(list[str]):
    """A list that counts everything but retains only small bounded samples."""

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


def _norm_header(cell: str) -> str:
    """Case-insensitive header identity with whitespace and pipe spacing tolerated."""
    segments = [" ".join(segment.split()).casefold() for segment in cell.split("|")]
    return "|".join(segments)


def _bounded(value: str) -> str:
    return " ".join(value.split())[:_VALUE_TEXT_LIMIT]


def _is_blank(value: str) -> bool:
    return not value.strip()


def _is_positive_number(value: str) -> bool:
    text = value.strip()
    if "_" in text:  # float() tolerates 1_000; the dictionary's 'numeric' does not
        return False
    try:
        number = float(text)
    except ValueError:
        return False
    return number > 0 and number != float("inf")


def _is_valid_count(value: str) -> bool:
    text = " ".join(value.strip().casefold().split())
    if text == "1 through 10":
        return True
    if not _COUNT_INTEGER_RE.fullmatch(text):
        return False
    return text == "0" or int(text) >= 11


def _parse_period(value: str | None, book: _FindingBook) -> date | None:
    if value is None or _is_blank(value):
        return None
    text = value.strip()
    try:
        if _ISO_DATE_RE.fullmatch(text):
            return date.fromisoformat(text)
        slash = _SLASH_DATE_RE.fullmatch(text)
        if slash:
            month, day, year = (int(part) for part in slash.groups())
            return date(year, month, day)
    except ValueError:
        # A shape that matched but is not a real calendar date falls through to the finding.
        pass
    book.add(
        "CMS_CSV_LAST_UPDATED_ON_INVALID",
        f"The last_updated_on value {text[:40]!r} is not an ISO YYYY-MM-DD or M/D/YYYY date.",
    )
    return None


def _one_year_after(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:  # February 29 has no anniversary in a non-leap year.
        return value.replace(year=value.year + 1, day=28)


def _add_freshness_finding(period: date | None, as_of: date, book: _FindingBook) -> None:
    if period is None:
        book.add(
            "CSV_FRESHNESS_DATE_NOT_USABLE",
            "Freshness cannot be assessed because last_updated_on is absent or invalid.",
        )
    elif period > as_of:
        book.add(
            "FRESHNESS_DATE_IN_FUTURE",
            f"The source update date {period.isoformat()} is after as_of {as_of.isoformat()}.",
        )
    elif as_of > _one_year_after(period):
        book.add(
            "FRESHNESS_ANNUAL_UPDATE_OVERDUE",
            f"The source date {period.isoformat()} is more than one year before "
            f"as_of {as_of.isoformat()}.",
        )


def _add_interpretability_findings(counts: _Counts, book: _FindingBook) -> None:
    if counts.payer_rate_count == 0:
        book.add(
            "CSV_INTERPRETABILITY_NO_PAYER_RATES",
            "No payer-specific rate values were observed in the streamed data rows.",
        )
    if counts.percentage_rate_count:
        book.add(
            "CSV_INTERPRETABILITY_PERCENTAGE_RATES",
            "Percentage-encoded payer rates are not directly dollar-denominated and are counted "
            "separately.",
        )
    if counts.algorithm_rate_count:
        book.add(
            "CSV_INTERPRETABILITY_ALGORITHM_RATES",
            "Algorithm-encoded payer rates are not directly dollar-denominated and are counted "
            "separately.",
        )


@dataclass(frozen=True)
class _GeneralElements:
    """What rows 1 and 2 declared, resolved by header name rather than position."""

    values: Mapping[str, str]
    license_state: str | None
    license_placeholder: bool
    attestation_value: str | None
    attestation_header_found: bool
    display: Mapping[str, str]


def _resolve_general(header_row: list[str], value_row: list[str]) -> _GeneralElements:
    values: dict[str, str] = {}
    display: dict[str, str] = {}
    license_state: str | None = None
    license_placeholder = False
    attestation_value: str | None = None
    attestation_found = False
    attestation_norm = _norm_header(ATTESTATION_HEADER_TEXT)
    for index, cell in enumerate(header_row):
        name = _norm_header(cell)
        if not name:
            continue
        value = value_row[index] if index < len(value_row) else ""
        if name == attestation_norm:
            attestation_found = True
            attestation_value = value
            display["attestation"] = _bounded(value)
            continue
        segments = name.split("|")
        if segments[0] == "license_number" and len(segments) == 2:
            state = segments[1]
            if _STATE_RE.fullmatch(state):
                license_state = state.upper()
            elif state in {"[state]", "state"}:
                license_placeholder = True
            values["license_number"] = value
            display["license_number"] = _bounded(value)
            display["license_state"] = license_state or state
            continue
        values[name] = value
        display[name] = _bounded(value)
    return _GeneralElements(
        values=values,
        license_state=license_state,
        license_placeholder=license_placeholder,
        attestation_value=attestation_value,
        attestation_header_found=attestation_found,
        display=display,
    )


def _general_field_usable(general: _GeneralElements, field_name: str) -> bool:
    if field_name == "attestation":
        if not general.attestation_header_found or general.attestation_value is None:
            return False
        return general.attestation_value.strip().casefold() in {"true", "false"}
    if field_name == "license_number":
        # The dictionary allows a blank license value but requires the header with its state.
        return general.license_state is not None
    value = general.values.get(field_name)
    return value is not None and not _is_blank(value)


def _inspect_general(general: _GeneralElements, book: _FindingBook) -> tuple[str, ...]:
    missing: list[str] = []
    for field_name in _REQUIRED_GENERAL_FIELDS:
        if _general_field_usable(general, field_name):
            continue
        missing.append(field_name)
        book.add(
            f"CMS_CSV_GENERAL_{field_name.upper()}_MISSING",
            f"Required general data element {field_name!r} is absent or unusable.",
        )
    if general.license_placeholder:
        book.add(
            "CMS_CSV_PLACEHOLDER_NOT_REPLACED",
            "The license_number|[state] header was published with its placeholder unreplaced.",
        )
    attestation = (general.attestation_value or "").strip().casefold()
    if general.attestation_header_found and attestation == "false":
        book.add(
            "CMS_CSV_ATTESTATION_NOT_CONFIRMED",
            "The required attestation value is explicitly false.",
        )
    version = general.values.get("version")
    if version is not None and not _is_blank(version) and version.strip() != "3.0.0":
        book.add(
            "CMS_CSV_VERSION_UNEXPECTED",
            f"Template version is {version.strip()[:40]!r}; this inspector implements CMS CSV "
            "v3.0.0.",
        )
    return tuple(missing)


@dataclass(frozen=True)
class _WideColumn:
    """One parameterized Wide header: which element, for which payer-and-plan combination."""

    element: str
    combo: str


@dataclass
class _ChargeHeader:
    """The resolved row-3 layout: column meanings by index, and what is missing."""

    layout: str | None = None
    ambiguous: bool = False
    by_name: dict[str, int] = field(default_factory=dict)
    code_pairs: list[tuple[int | None, int | None]] = field(default_factory=list)
    wide_columns: dict[int, _WideColumn] = field(default_factory=dict)
    combos: dict[str, dict[str, int]] = field(default_factory=dict)
    missing_columns: list[str] = field(default_factory=list)
    placeholder_headers: int = 0
    width: int = 0


_CODE_RE = re.compile(r"^code\|(\d+)(\|type)?$")


def _classify_wide(segments: list[str]) -> _WideColumn | None:
    if len(segments) == 4 and segments[0] == "standard_charge":
        element = f"standard_charge|{segments[3]}"
        if element in _WIDE_COMBO_ELEMENTS:
            return _WideColumn(element, f"{segments[1]}|{segments[2]}")
    if len(segments) == 3 and segments[0] in {
        "median_amount",
        "10th_percentile",
        "90th_percentile",
        "count",
        "additional_payer_notes",
    }:
        return _WideColumn(segments[0], f"{segments[1]}|{segments[2]}")
    return None


def _resolve_charge_header(cells: list[str], book: _FindingBook) -> _ChargeHeader:
    header = _ChargeHeader(width=len(cells))
    codes: dict[str, tuple[int | None, int | None]] = {}
    for index, cell in enumerate(cells):
        name = _norm_header(cell)
        if not name:
            continue
        if "[i]" in name or "[payer_name]" in name or "[plan_name]" in name or "[state]" in name:
            header.placeholder_headers += 1
            book.add(
                "CMS_CSV_PLACEHOLDER_NOT_REPLACED",
                f"Header {cell.strip()[:60]!r} was published with its placeholder unreplaced.",
            )
            continue
        code_match = _CODE_RE.fullmatch(name)
        if code_match:
            ordinal = code_match.group(1)
            pair = codes.get(ordinal, (None, None))
            codes[ordinal] = (pair[0], index) if code_match.group(2) else (index, pair[1])
            continue
        wide = _classify_wide(name.split("|"))
        if wide is not None:
            header.wide_columns[index] = wide
            header.combos.setdefault(wide.combo, {})[wide.element] = index
            continue
        if name in header.by_name:
            book.add(
                "CMS_CSV_HEADER_NOT_UNIQUE",
                f"Column header {cell.strip()[:60]!r} appears more than once.",
            )
            continue
        header.by_name[name] = index
    header.code_pairs = [codes[key] for key in sorted(codes, key=_code_sort_key)]
    _resolve_layout(header, book)
    _require_columns(header, book)
    return header


def _code_sort_key(ordinal: str) -> tuple[int, str]:
    return (int(ordinal), "") if ordinal.isdigit() else (1 << 30, ordinal)


def _resolve_layout(header: _ChargeHeader, book: _FindingBook) -> None:
    has_tall = "payer_name" in header.by_name or "plan_name" in header.by_name
    has_wide = bool(header.wide_columns)
    if has_tall and has_wide:
        header.ambiguous = True
        header.layout = "wide"
        book.add(
            "CMS_CSV_LAYOUT_AMBIGUOUS",
            "The charge header row mixes Tall payer_name/plan_name columns with Wide "
            "payer-specific headers; it was read as Wide.",
        )
        return
    header.layout = "wide" if has_wide else "tall"


def _require_columns(header: _ChargeHeader, book: _FindingBook) -> None:
    required = list(_SHARED_REQUIRED_COLUMNS)
    if header.layout == "tall":
        required.extend(_TALL_REQUIRED_COLUMNS)
    for column in required:
        if column not in header.by_name:
            header.missing_columns.append(column)
            book.add(
                f"CMS_CSV_COLUMN_{_column_code(column)}_MISSING",
                f"Required standard-charge column {column!r} is absent from the header row.",
            )
    if not any(code is not None and kind is not None for code, kind in header.code_pairs):
        header.missing_columns.append("code|1")
        book.add(
            "CMS_CSV_COLUMN_CODE_PAIR_MISSING",
            "No complete code|1 and code|1|type column pair is present in the header row.",
        )
    if header.layout == "wide":
        for combo, elements in sorted(header.combos.items()):
            missing = [element for element in _WIDE_COMBO_ELEMENTS if element not in elements]
            if missing:
                book.add(
                    "CMS_CSV_WIDE_PAYER_HEADER_SET_INCOMPLETE",
                    f"The payer-and-plan combination {combo[:80]!r} is missing "
                    f"{len(missing)} of its nine required headers, first: {missing[0]!r}.",
                )


class _Row:
    """One data row resolved against the charge header, with blank-tolerant access."""

    def __init__(self, cells: list[str], header: _ChargeHeader) -> None:
        self.cells = cells
        self.header = header

    def get(self, name: str) -> str:
        index = self.header.by_name.get(name)
        if index is None or index >= len(self.cells):
            return ""
        return self.cells[index]

    def has(self, name: str) -> bool:
        return not _is_blank(self.get(name))

    def wide_value(self, combo: str, element: str) -> str:
        index = self.header.combos.get(combo, {}).get(element)
        if index is None or index >= len(self.cells):
            return ""
        return self.cells[index]


def _check_row_width(row: _Row, book: _FindingBook, problems: _CappedProblems) -> None:
    extra = row.cells[row.header.width :]
    if any(not _is_blank(cell) for cell in extra):
        book.add(
            "CMS_CSV_ROW_WIDTH_MISMATCH",
            "A data row carries non-blank cells beyond the declared columns.",
        )
        problems.append(f"row with {len(row.cells)} cells against {row.header.width} headers")


def _check_enums(row: _Row, counts: _Counts, book: _FindingBook) -> None:
    setting = row.get("setting").strip().casefold()
    if setting in ACCEPTED_SETTINGS:
        counts.settings_seen.add(setting)
    elif setting:
        book.add(
            "CMS_CSV_SETTING_INVALID",
            f"A setting value is not one of {sorted(ACCEPTED_SETTINGS)!r}; "
            f"first value: {setting[:40]!r}.",
        )
    drug_type = row.get("drug_type_of_measurement").strip().casefold()
    if drug_type and drug_type not in ACCEPTED_DRUG_TYPES:
        book.add(
            "CMS_CSV_DRUG_TYPE_INVALID",
            f"A drug type of measurement is not in the CMS accepted set; "
            f"first value: {drug_type[:40]!r}.",
        )
    for _, type_index in row.header.code_pairs:
        if type_index is None or type_index >= len(row.cells):
            continue
        code_type = row.cells[type_index].strip().casefold()
        if code_type and code_type not in ACCEPTED_CODE_TYPES:
            book.add(
                "CMS_CSV_CODE_TYPE_INVALID",
                f"A code-type value is not in the CMS accepted set; "
                f"first value: {code_type[:40]!r}.",
            )


def _check_numeric(row: _Row, book: _FindingBook) -> None:
    for column in _NUMERIC_COLUMNS:
        value = row.get(column)
        if not _is_blank(value) and not _is_positive_number(value):
            book.add(
                "CMS_CSV_NUMERIC_VALUE_INVALID",
                f"Column {column!r} holds a value that is not a positive number; "
                f"first value: {value.strip()[:40]!r}.",
            )
    for index, wide in row.header.wide_columns.items():
        if wide.element in {"count", "additional_payer_notes"}:
            continue
        if wide.element.endswith("algorithm") or wide.element.endswith("methodology"):
            continue
        if index >= len(row.cells):
            continue
        value = row.cells[index]
        if not _is_blank(value) and not _is_positive_number(value):
            book.add(
                "CMS_CSV_NUMERIC_VALUE_INVALID",
                f"Column {wide.element!r} holds a value that is not a positive number; "
                f"first value: {value.strip()[:40]!r}.",
            )


def _cell_at(cells: list[str], index: int | None) -> str:
    return cells[index] if index is not None and index < len(cells) else ""


def _row_code_facts(row: _Row) -> tuple[int, bool, bool]:
    """How many complete code pairs, whether any is unpaired, and whether any is NDC."""
    complete = 0
    unpaired = False
    has_ndc = False
    for code_index, type_index in row.header.code_pairs:
        code = _cell_at(row.cells, code_index)
        kind = _cell_at(row.cells, type_index)
        code_present = not _is_blank(code)
        kind_present = not _is_blank(kind)
        if code_present and kind_present:
            complete += 1
            if kind.strip().casefold() == "ndc":
                has_ndc = True
        elif code_present or kind_present:
            unpaired = True
    return complete, unpaired, has_ndc


@dataclass(frozen=True)
class _PayerFacts:
    """The payer-specific facts of one row, uniform across Tall and Wide."""

    any_charge: bool
    any_dollar: bool
    context_missing: bool
    payer_named_without_charge: bool
    other_without_notes: bool
    derived_missing_count: bool
    derived_missing_percentiles: bool
    zero_count_without_notes: bool
    invalid_count: bool
    invalid_methodology: bool
    methodologies: tuple[str, ...]
    dollar_cells: int
    percentage_cells: int
    algorithm_cells: int
    payer_entries: int


def _tall_payer_facts(row: _Row) -> _PayerFacts:
    dollar = row.get("standard_charge|negotiated_dollar")
    percentage = row.get("standard_charge|negotiated_percentage")
    algorithm = row.get("standard_charge|negotiated_algorithm")
    has_any_charge = not (_is_blank(dollar) and _is_blank(percentage) and _is_blank(algorithm))
    methodology = row.get("standard_charge|methodology").strip().casefold()
    generic_notes = row.has("additional_generic_notes")
    count_value = row.get("count")
    is_derived = not (_is_blank(percentage) and _is_blank(algorithm))
    percentiles_present = all(
        row.has(name) for name in ("median_amount", "10th_percentile", "90th_percentile")
    )
    count_is_zero = count_value.strip() == "0"
    valid_methodology = methodology in ACCEPTED_METHODOLOGIES
    return _PayerFacts(
        any_charge=has_any_charge,
        any_dollar=not _is_blank(dollar),
        context_missing=has_any_charge
        and not (row.has("payer_name") and row.has("plan_name") and bool(methodology)),
        payer_named_without_charge=(row.has("payer_name") or row.has("plan_name"))
        and not has_any_charge,
        other_without_notes=methodology == "other" and not generic_notes,
        derived_missing_count=is_derived and _is_blank(count_value),
        derived_missing_percentiles=is_derived
        and not _is_blank(count_value)
        and not count_is_zero
        and not percentiles_present,
        zero_count_without_notes=is_derived and count_is_zero and not generic_notes,
        invalid_count=not _is_blank(count_value) and not _is_valid_count(count_value),
        invalid_methodology=bool(methodology) and not valid_methodology,
        methodologies=(methodology,) if valid_methodology else (),
        dollar_cells=0 if _is_blank(dollar) else 1,
        percentage_cells=0 if _is_blank(percentage) else 1,
        algorithm_cells=0 if _is_blank(algorithm) else 1,
        payer_entries=1 if has_any_charge or row.has("payer_name") or row.has("plan_name") else 0,
    )


def _wide_combo_facts(row: _Row, combo: str, generic_notes: bool) -> _PayerFacts:
    dollar = row.wide_value(combo, "standard_charge|negotiated_dollar")
    percentage = row.wide_value(combo, "standard_charge|negotiated_percentage")
    algorithm = row.wide_value(combo, "standard_charge|negotiated_algorithm")
    has_any_charge = not (_is_blank(dollar) and _is_blank(percentage) and _is_blank(algorithm))
    methodology = row.wide_value(combo, "standard_charge|methodology").strip().casefold()
    notes = generic_notes or not _is_blank(row.wide_value(combo, "additional_payer_notes"))
    count_value = row.wide_value(combo, "count")
    is_derived = not (_is_blank(percentage) and _is_blank(algorithm))
    percentiles_present = all(
        not _is_blank(row.wide_value(combo, name))
        for name in ("median_amount", "10th_percentile", "90th_percentile")
    )
    count_is_zero = count_value.strip() == "0"
    valid_methodology = methodology in ACCEPTED_METHODOLOGIES
    return _PayerFacts(
        any_charge=has_any_charge,
        any_dollar=not _is_blank(dollar),
        context_missing=has_any_charge and not bool(methodology),
        payer_named_without_charge=False,
        other_without_notes=methodology == "other" and not notes,
        derived_missing_count=is_derived and _is_blank(count_value),
        derived_missing_percentiles=is_derived
        and not _is_blank(count_value)
        and not count_is_zero
        and not percentiles_present,
        zero_count_without_notes=is_derived and count_is_zero and not notes,
        invalid_count=not _is_blank(count_value) and not _is_valid_count(count_value),
        invalid_methodology=bool(methodology) and not valid_methodology,
        methodologies=(methodology,) if valid_methodology else (),
        dollar_cells=0 if _is_blank(dollar) else 1,
        percentage_cells=0 if _is_blank(percentage) else 1,
        algorithm_cells=0 if _is_blank(algorithm) else 1,
        payer_entries=1 if has_any_charge else 0,
    )


def _apply_payer_facts(facts: _PayerFacts, counts: _Counts, book: _FindingBook) -> None:
    if facts.context_missing:
        book.add(
            "CMS_CSV_PAYER_CONTEXT_MISSING",
            "A payer-specific charge is encoded without its payer name, plan name, or methodology.",
        )
    if facts.payer_named_without_charge:
        book.add(
            "CMS_CSV_PAYER_WITHOUT_CHARGE",
            "A payer or plan name is encoded with no payer-specific charge beside it.",
        )
    if facts.other_without_notes:
        book.add(
            "CMS_CSV_OTHER_METHODOLOGY_NOTES_MISSING",
            "A methodology of 'other' lacks the required explanatory note.",
        )
    if facts.derived_missing_count:
        book.add(
            "CMS_CSV_DERIVED_RATE_COUNT_MISSING",
            "A percentage or algorithm charge lacks the required allowed-amount count.",
        )
    if facts.derived_missing_percentiles:
        book.add(
            "CMS_CSV_DERIVED_RATE_PERCENTILES_MISSING",
            "A percentage or algorithm charge with a nonzero count lacks its allowed-amount "
            "median, 10th percentile, or 90th percentile.",
        )
    if facts.zero_count_without_notes:
        book.add(
            "CMS_CSV_ZERO_COUNT_NOTES_MISSING",
            "A derived rate with count '0' lacks the required explanatory note.",
        )
    if facts.invalid_count:
        book.add(
            "CMS_CSV_COUNT_VALUE_INVALID",
            "A count of allowed amounts is not '0', '1 through 10', or a whole number over ten.",
        )
    if facts.invalid_methodology:
        book.add(
            "CMS_CSV_METHODOLOGY_INVALID",
            f"A standard-charge methodology is not in {sorted(ACCEPTED_METHODOLOGIES)!r}.",
        )
    counts.methodologies_seen.update(facts.methodologies)
    counts.dollar_rate_count += facts.dollar_cells
    counts.percentage_rate_count += facts.percentage_cells
    counts.algorithm_rate_count += facts.algorithm_cells
    counts.payer_rate_count += facts.payer_entries


def _inspect_data_row(
    row: _Row, counts: _Counts, book: _FindingBook, problems: _CappedProblems
) -> None:
    counts.row_count += 1
    _check_row_width(row, book, problems)
    _check_enums(row, counts, book)
    _check_numeric(row, book)

    complete_codes, unpaired, has_ndc = _row_code_facts(row)
    counts.code_count += complete_codes
    if unpaired:
        book.add(
            "CMS_CSV_CODE_TYPE_UNPAIRED",
            "A code without its code type, or a code type without its code.",
        )

    generic_notes = row.has("additional_generic_notes")
    facts_list = (
        [_wide_combo_facts(row, combo, generic_notes) for combo in sorted(row.header.combos)]
        if row.header.layout == "wide"
        else [_tall_payer_facts(row)]
    )
    any_payer_charge = any(facts.any_charge for facts in facts_list)
    any_dollar = any(facts.any_dollar for facts in facts_list)
    for facts in facts_list:
        _apply_payer_facts(facts, counts, book)

    base_charge = row.has("standard_charge|gross") or row.has("standard_charge|discounted_cash")
    any_charge = base_charge or any_payer_charge
    is_item = complete_codes > 0
    modifier_row = row.has("modifiers") and not is_item
    if is_item:
        counts.item_count += 1
    if any_charge and complete_codes == 0 and not modifier_row:
        book.add(
            "CMS_CSV_CODE_PAIRING_MISSING",
            "A row with a standard charge has no complete code and code-type pairing.",
        )
    _check_item_completeness(
        row,
        book,
        is_item=is_item,
        any_charge=any_charge,
        any_payer_charge=any_payer_charge,
    )
    if any_dollar and not (row.has("standard_charge|min") and row.has("standard_charge|max")):
        book.add(
            "CMS_CSV_DOLLAR_RANGE_MISSING",
            "A row with a dollar-denominated payer rate lacks the de-identified minimum or "
            "maximum negotiated charge.",
        )
    unit = row.get("drug_unit_of_measurement")
    kind = row.get("drug_type_of_measurement")
    if _is_blank(unit) != _is_blank(kind):
        book.add(
            "CMS_CSV_DRUG_FIELDS_UNPAIRED",
            "A drug unit of measurement without its type of measurement, or the reverse.",
        )
    if has_ndc and (_is_blank(unit) or _is_blank(kind)):
        book.add(
            "CMS_CSV_NDC_DRUG_FIELDS_MISSING",
            "An NDC-coded row lacks its drug unit or type of measurement.",
        )


def _check_item_completeness(
    row: _Row,
    book: _FindingBook,
    *,
    is_item: bool,
    any_charge: bool,
    any_payer_charge: bool,
) -> None:
    has_description = row.has("description")
    if is_item or any_charge:
        if not has_description:
            book.add(
                "CMS_CSV_DESCRIPTION_MISSING",
                "A data row with codes or charges has no usable description.",
            )
        if not any_charge:
            book.add(
                "CMS_CSV_CHARGE_VALUE_MISSING",
                "An encoded item or service row carries no gross, cash, or payer-specific charge.",
            )
        if any_charge and not row.has("setting"):
            book.add(
                "CMS_CSV_SETTING_INVALID",
                "A setting cell is blank where blanks are not accepted.",
            )
        return
    if row.has("modifiers"):
        allowed = (
            any_payer_charge
            or row.has("additional_generic_notes")
            or any(
                not _is_blank(row.wide_value(combo, "additional_payer_notes"))
                for combo in row.header.combos
            )
        )
        if not (has_description and allowed):
            book.add(
                "CMS_CSV_MODIFIER_ROW_CONTEXT_MISSING",
                "A modifier row without an item or service lacks the required description or "
                "accompanying charge or note.",
            )
    elif has_description and not any_charge:
        book.add(
            "CMS_CSV_CHARGE_VALUE_MISSING",
            "An encoded item or service row carries no gross, cash, or payer-specific charge.",
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
    findings: tuple[Finding, ...], *, scan_completed: bool, row_count: int
) -> FileScorecard:
    completeness_status: DimensionStatus | None = None
    interpretability_status: DimensionStatus | None = None
    if not scan_completed:
        completeness_status = "NOT_ASSESSED"
        interpretability_status = "NOT_ASSESSED"
    elif row_count == 0:
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


class _Scan:
    """Mutable single-pass scan state, so an encoding restart replaces it wholesale."""

    def __init__(self) -> None:
        self.book = _FindingBook()
        self.counts = _Counts()
        self.problems = _CappedProblems()
        self.general: _GeneralElements | None = None
        self.header: _ChargeHeader | None = None
        self.stream_error: str | None = None


def _read_rows(handle: io.TextIOBase) -> Iterator[list[str]]:
    yield from csv.reader(handle)


def _run_scan(source_path: Path, encoding: str) -> _Scan:
    scan = _Scan()
    row_index = 0
    general_header: list[str] = []
    general_values: list[str] = []
    with source_path.open("rb") as binary:
        text = io.TextIOWrapper(binary, encoding=encoding, newline="")
        try:
            for cells in _read_rows(text):
                row_index += 1
                if row_index == 1:
                    general_header = cells
                elif row_index == 2:
                    general_values = cells
                elif row_index == 3:
                    scan.header = _resolve_charge_header(cells, scan.book)
                elif any(not _is_blank(cell) for cell in cells):
                    if scan.header is not None:
                        _inspect_data_row(
                            _Row(cells, scan.header), scan.counts, scan.book, scan.problems
                        )
        except (csv.Error, UnicodeDecodeError, MemoryError) as exc:
            scan.stream_error = f"{type(exc).__name__}: {exc}"
            scan.problems.append(f"stream error: {scan.stream_error}")
        finally:
            text.detach()
    scan.general = _resolve_general(general_header, general_values)
    if scan.header is None and scan.stream_error is None:
        scan.book.add(
            "CMS_CSV_CHARGE_HEADER_ROW_MISSING",
            "The file ends before row 3, so no standard-charge column headers exist.",
        )
    return scan


def _detect_bom(source_path: Path) -> bool:
    with source_path.open("rb") as handle:
        return handle.read(3) == b"\xef\xbb\xbf"


def inspect_hospital_csv_file(
    path: str | Path,
    publisher: PublisherRef | None = None,
    *,
    as_of: date,
) -> CsvFileInspection:
    """Inspect one local CMS hospital CSV v3 file (Tall or Wide) with bounded memory.

    ``as_of`` is required so freshness is repeatable rather than dependent on the wall clock.
    Filesystem errors are allowed to propagate: retrievability means remote publication access and
    is explicitly ``NOT_ASSESSED`` by this local-only function.
    """
    source_path = Path(path)
    source_sha256, source_size = _hash_source(source_path)
    had_bom = _detect_bom(source_path)

    prior_limit = csv.field_size_limit(_FIELD_SIZE_LIMIT)
    try:
        encoding = "utf-8"
        scan = _run_scan(source_path, "utf-8-sig")
        if scan.stream_error is not None and "UnicodeDecodeError" in scan.stream_error:
            encoding = "latin-1"
            scan = _run_scan(source_path, encoding)
            scan.book.add(
                "CMS_CSV_ENCODING_NOT_UTF8",
                "The file is not valid UTF-8; it was decoded as Latin-1 and recorded.",
            )
    finally:
        csv.field_size_limit(prior_limit)

    general = scan.general
    if general is None:  # pragma: no cover - _run_scan always resolves the general elements
        raise RuntimeError("scan finished without resolving general elements")
    missing_general = _inspect_general(general, scan.book)
    period = _parse_period(general.values.get("last_updated_on"), scan.book)
    raw_version = general.values.get("version")
    version = raw_version.strip() if raw_version is not None and raw_version.strip() else None

    scan_completed = scan.stream_error is None
    if scan.stream_error is not None:
        scan.book.add(
            "CMS_CSV_STREAM_INCOMPLETE",
            f"The CSV table could not be completely streamed: {scan.stream_error[:160]}",
        )
    if scan_completed and scan.counts.row_count == 0:
        scan.book.add(
            "CMS_CSV_TABLE_EMPTY",
            "The table contains no usable data rows after the header rows.",
        )
    if had_bom:
        scan.book.add(
            "CMS_CSV_UTF8_BOM_PRESENT",
            "The source begins with a UTF-8 byte-order mark; it was tolerated and recorded.",
        )
    _add_freshness_finding(period, as_of, scan.book)
    _add_interpretability_findings(scan.counts, scan.book)

    findings = scan.book.findings()
    scorecard = _scorecard(findings, scan_completed=scan_completed, row_count=scan.counts.row_count)
    header = scan.header
    return CsvFileInspection(
        source_path=str(source_path),
        source_sha256=source_sha256,
        source_size=source_size,
        publisher=publisher,
        as_of=as_of,
        envelope=dict(general.display),
        version=version,
        period=period,
        layout=header.layout if header is not None else None,
        row_count=scan.counts.row_count,
        item_count=scan.counts.item_count,
        code_count=scan.counts.code_count,
        payer_plan_combination_count=len(header.combos) if header is not None else 0,
        payer_rate_count=scan.counts.payer_rate_count,
        dollar_rate_count=scan.counts.dollar_rate_count,
        percentage_rate_count=scan.counts.percentage_rate_count,
        algorithm_rate_count=scan.counts.algorithm_rate_count,
        settings_seen=tuple(sorted(scan.counts.settings_seen)),
        methodologies_seen=tuple(sorted(scan.counts.methodologies_seen)),
        missing_general_fields=missing_general,
        missing_columns=tuple(header.missing_columns) if header is not None else (),
        had_bom=had_bom,
        encoding=encoding,
        scan_completed=scan_completed,
        problem_count=scan.problems.total_count,
        problems=tuple(scan.problems),
        scorecard=scorecard,
    )
