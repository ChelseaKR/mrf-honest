"""Bounded-memory normalization of CMS hospital JSON into relational spool files.

The spool is deliberately boring TSV.  It is an intermediate build artifact, not a published
format: rows are written as each source item is yielded, then DuckDB bulk-loads them under an
explicit schema.  Peak Python memory therefore remains bounded by one source item.
"""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from mrf_honest.stream import StreamItem, StreamStats, stream_array_entries

ARRAY_KEY = "standard_charge_information"
MODIFIER_ARRAY_KEY = "modifier_information"
NORMALIZATION_POLICY_VERSION = "cms-hpt-json-v3-decimal10-modifier-v1"
# This is a TSV sentinel, not a credential. Inputs using the exact token are refused.
NULL_TOKEN = "__MRF_HONEST_NULL__"  # noqa: S105

RAW_COLUMNS = (
    "run_id",
    "source_file_id",
    "publisher_id",
    "period",
    "file_version",
    "item_ordinal",
    "payload_text",
    "payload_sha256",
)
RAW_MODIFIER_COLUMNS = (
    "run_id",
    "source_file_id",
    "publisher_id",
    "period",
    "file_version",
    "modifier_ordinal",
    "payload_text",
    "payload_sha256",
)
ITEM_COLUMNS = (
    "run_id",
    "source_file_id",
    "publisher_id",
    "item_id",
    "item_ordinal",
    "description",
    "drug_unit",
    "drug_type",
)
CODE_COLUMNS = (
    "run_id",
    "source_file_id",
    "publisher_id",
    "item_id",
    "code_ordinal",
    "code",
    "code_type",
)
CHARGE_COLUMNS = (
    "run_id",
    "source_file_id",
    "publisher_id",
    "item_id",
    "charge_group_id",
    "charge_ordinal",
    "minimum_amount",
    "maximum_amount",
    "gross_charge",
    "discounted_cash",
    "setting",
    "billing_class",
    "modifier_codes_json",
    "additional_generic_notes",
)
PAYER_COLUMNS = (
    "run_id",
    "source_file_id",
    "publisher_id",
    "charge_group_id",
    "payer_rate_id",
    "payer_ordinal",
    "payer_name",
    "plan_name",
    "methodology",
    "standard_charge_dollar",
    "standard_charge_percentage",
    "standard_charge_algorithm",
    "median_amount",
    "p10_amount",
    "p90_amount",
    "allowed_count",
    "additional_payer_notes",
    "canonical_payer_plan_key",
)
MODIFIER_COLUMNS = (
    "run_id",
    "source_file_id",
    "publisher_id",
    "modifier_id",
    "modifier_ordinal",
    "code",
    "canonical_code",
    "description",
    "setting",
)
MODIFIER_PAYER_COLUMNS = (
    "run_id",
    "source_file_id",
    "publisher_id",
    "modifier_id",
    "modifier_payer_id",
    "payer_ordinal",
    "payer_name",
    "plan_name",
    "canonical_payer_plan_key",
    "description",
)
CHARGE_MODIFIER_COLUMNS = (
    "run_id",
    "source_file_id",
    "publisher_id",
    "charge_group_id",
    "modifier_ordinal",
    "modifier_code",
    "canonical_modifier_code",
)

SPOOL_COLUMNS: dict[str, tuple[str, ...]] = {
    "raw_hospital_items": RAW_COLUMNS,
    "raw_modifier_information": RAW_MODIFIER_COLUMNS,
    "stg_charge_item": ITEM_COLUMNS,
    "stg_charge_code": CODE_COLUMNS,
    "stg_charge_group": CHARGE_COLUMNS,
    "stg_payer_rate": PAYER_COLUMNS,
    "stg_modifier": MODIFIER_COLUMNS,
    "stg_modifier_payer": MODIFIER_PAYER_COLUMNS,
    "stg_charge_modifier": CHARGE_MODIFIER_COLUMNS,
}


class NormalizationError(ValueError):
    """The source cannot be represented without guessing or losing evidence."""


@dataclass(frozen=True)
class NormalizeContext:
    run_id: str
    source_file_id: str
    publisher_id: str
    period: str
    file_version: str


@dataclass(frozen=True)
class NormalizedCounts:
    source_bytes_read: int
    items: int
    codes: int
    charge_groups: int
    payer_rates: int
    modifiers: int
    modifier_payer_mappings: int
    charge_modifiers: int
    had_bom: bool

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "source_bytes_read": self.source_bytes_read,
            "items": self.items,
            "codes": self.codes,
            "charge_groups": self.charge_groups,
            "payer_rates": self.payer_rates,
            "modifiers": self.modifiers,
            "modifier_payer_mappings": self.modifier_payer_mappings,
            "charge_modifiers": self.charge_modifiers,
            "had_bom": self.had_bom,
        }


@dataclass
class _Counts:
    items: int = 0
    codes: int = 0
    charge_groups: int = 0
    payer_rates: int = 0
    modifiers: int = 0
    modifier_payer_mappings: int = 0
    charge_modifiers: int = 0


class _RowWriter(Protocol):
    def writerow(self, row: Iterable[object]) -> object: ...


class _Spool:
    def __init__(self, root: Path, stack: ExitStack) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.paths = {name: root / f"{name}.tsv" for name in SPOOL_COLUMNS}
        self._writers: dict[str, _RowWriter] = {}
        for name, columns in SPOOL_COLUMNS.items():
            handle = stack.enter_context(self.paths[name].open("w", encoding="utf-8", newline=""))
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(columns)
            self._writers[name] = writer

    def write(self, table: str, values: Sequence[object | None]) -> None:
        self._writers[table].writerow([NULL_TOKEN if value is None else value for value in values])


def _identifier(source_file_id: str, kind: str, *ordinals: int) -> str:
    material = ":".join((source_file_id, kind, *(str(value) for value in ordinals)))
    return hashlib.sha256(material.encode()).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise NormalizationError(f"{field} must be an object")
    return value


def _array(value: object, field: str, *, required: bool = False) -> list[Any]:
    if value is None and not required:
        return []
    if not isinstance(value, list):
        raise NormalizationError(f"{field} must be an array")
    if required and not value:
        raise NormalizationError(f"{field} must not be empty")
    return value


def _text(value: object, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise NormalizationError(f"{field} must be a non-empty string")
    if value == NULL_TOKEN:
        raise NormalizationError(f"{field} uses the reserved spool null token")
    return value


def _required_text(value: object, field: str) -> str:
    result = _text(value, field, required=True)
    if result is None:  # pragma: no cover - required=True rejects None
        raise NormalizationError(f"{field} must be a non-empty string")
    return result


def _number(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        raise NormalizationError(f"{field} must be numeric")
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite():
        raise NormalizationError(f"{field} must be finite")
    return result


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _base(context: NormalizeContext) -> tuple[str, str, str]:
    return context.run_id, context.source_file_id, context.publisher_id


def _canonical(value: str) -> str:
    """Derive a documented comparison key while retaining the publisher's exact text."""
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _payer_plan_key(payer_name: str, plan_name: str) -> str:
    return _json([_canonical(payer_name), _canonical(plan_name)])


def _raw_payload(entry: StreamItem) -> tuple[str, str]:
    text = entry.raw.decode("utf-8")
    return text, hashlib.sha256(entry.raw).hexdigest()


def _write_codes(
    spool: _Spool,
    context: NormalizeContext,
    item: Mapping[str, Any],
    item_id: str,
    item_ordinal: int,
) -> int:
    codes = _array(item.get("code_information"), "code_information", required=True)
    for code_ordinal, raw_code in enumerate(codes):
        code = _mapping(raw_code, f"item {item_ordinal} code {code_ordinal}")
        spool.write(
            "stg_charge_code",
            (
                *_base(context),
                item_id,
                code_ordinal,
                _text(code.get("code"), "code", required=True),
                _text(code.get("type"), "code type", required=True),
            ),
        )
    return len(codes)


def _write_payers(
    spool: _Spool,
    context: NormalizeContext,
    charge: Mapping[str, Any],
    charge_group_id: str,
    item_ordinal: int,
    charge_ordinal: int,
) -> int:
    payers = _array(charge.get("payers_information"), "payers_information")
    for payer_ordinal, raw_payer in enumerate(payers):
        label = f"item {item_ordinal} charge {charge_ordinal} payer {payer_ordinal}"
        payer = _mapping(raw_payer, label)
        payer_rate_id = _identifier(
            context.source_file_id, "payer", item_ordinal, charge_ordinal, payer_ordinal
        )
        payer_name = _required_text(payer.get("payer_name"), f"{label} payer_name")
        plan_name = _required_text(payer.get("plan_name"), f"{label} plan_name")
        spool.write(
            "stg_payer_rate",
            (
                *_base(context),
                charge_group_id,
                payer_rate_id,
                payer_ordinal,
                payer_name,
                plan_name,
                _text(payer.get("methodology"), f"{label} methodology", required=True),
                _number(payer.get("standard_charge_dollar"), f"{label} dollar"),
                _number(payer.get("standard_charge_percentage"), f"{label} percentage"),
                _text(payer.get("standard_charge_algorithm"), f"{label} algorithm"),
                _number(payer.get("median_amount"), f"{label} median"),
                _number(payer.get("10th_percentile"), f"{label} 10th percentile"),
                _number(payer.get("90th_percentile"), f"{label} 90th percentile"),
                _text(payer.get("count"), f"{label} count"),
                _text(payer.get("additional_payer_notes"), f"{label} notes"),
                _payer_plan_key(payer_name, plan_name),
            ),
        )
    return len(payers)


def _write_charges(
    spool: _Spool,
    context: NormalizeContext,
    item: Mapping[str, Any],
    item_id: str,
    item_ordinal: int,
) -> tuple[int, int, int]:
    charges = _array(item.get("standard_charges"), "standard_charges", required=True)
    payer_count = 0
    modifier_count = 0
    for charge_ordinal, raw_charge in enumerate(charges):
        label = f"item {item_ordinal} charge {charge_ordinal}"
        charge = _mapping(raw_charge, label)
        charge_group_id = _identifier(
            context.source_file_id, "charge", item_ordinal, charge_ordinal
        )
        modifier_codes = _array(charge.get("modifier_code"), f"{label} modifier_code")
        if any(not isinstance(code, str) for code in modifier_codes):
            raise NormalizationError(f"{label} modifier_code values must be strings")
        for modifier_ordinal, modifier_code in enumerate(modifier_codes):
            if not modifier_code.strip():
                raise NormalizationError(f"{label} modifier_code values must not be empty")
            spool.write(
                "stg_charge_modifier",
                (
                    *_base(context),
                    charge_group_id,
                    modifier_ordinal,
                    modifier_code,
                    _canonical(modifier_code),
                ),
            )
            modifier_count += 1
        spool.write(
            "stg_charge_group",
            (
                *_base(context),
                item_id,
                charge_group_id,
                charge_ordinal,
                _number(charge.get("minimum"), f"{label} minimum"),
                _number(charge.get("maximum"), f"{label} maximum"),
                _number(charge.get("gross_charge"), f"{label} gross_charge"),
                _number(charge.get("discounted_cash"), f"{label} discounted_cash"),
                _text(charge.get("setting"), f"{label} setting", required=True),
                _text(charge.get("billing_class"), f"{label} billing_class"),
                _json(modifier_codes),
                _text(charge.get("additional_generic_notes"), f"{label} notes"),
            ),
        )
        payer_count += _write_payers(
            spool, context, charge, charge_group_id, item_ordinal, charge_ordinal
        )
    return len(charges), payer_count, modifier_count


def _write_item(
    spool: _Spool,
    context: NormalizeContext,
    entry: StreamItem,
) -> tuple[int, int, int, int]:
    item = entry.value
    item_ordinal = entry.ordinal
    item_id = _identifier(context.source_file_id, "item", item_ordinal)
    drug = item.get("drug_information")
    drug_mapping = _mapping(drug, "drug_information") if drug is not None else {}
    payload_text, payload_sha256 = _raw_payload(entry)
    spool.write(
        "raw_hospital_items",
        (
            context.run_id,
            context.source_file_id,
            context.publisher_id,
            context.period,
            context.file_version,
            item_ordinal,
            payload_text,
            payload_sha256,
        ),
    )
    spool.write(
        "stg_charge_item",
        (
            *_base(context),
            item_id,
            item_ordinal,
            _text(item.get("description"), "description", required=True),
            _number(drug_mapping.get("unit"), "drug unit"),
            _text(drug_mapping.get("type"), "drug type"),
        ),
    )
    code_count = _write_codes(spool, context, item, item_id, item_ordinal)
    charge_count, payer_count, modifier_count = _write_charges(
        spool, context, item, item_id, item_ordinal
    )
    return code_count, charge_count, payer_count, modifier_count


def _write_modifier(
    spool: _Spool,
    context: NormalizeContext,
    entry: StreamItem,
) -> int:
    modifier = entry.value
    ordinal = entry.ordinal
    label = f"modifier {ordinal}"
    modifier_id = _identifier(context.source_file_id, "modifier", ordinal)
    code = _required_text(modifier.get("code"), f"{label} code")
    description = _required_text(modifier.get("description"), f"{label} description")
    payload_text, payload_sha256 = _raw_payload(entry)
    spool.write(
        "raw_modifier_information",
        (
            context.run_id,
            context.source_file_id,
            context.publisher_id,
            context.period,
            context.file_version,
            ordinal,
            payload_text,
            payload_sha256,
        ),
    )
    spool.write(
        "stg_modifier",
        (
            *_base(context),
            modifier_id,
            ordinal,
            code,
            _canonical(code),
            description,
            _text(modifier.get("setting"), f"{label} setting"),
        ),
    )
    mappings = _array(
        modifier.get("modifier_payer_information"),
        f"{label} modifier_payer_information",
        required=True,
    )
    for payer_ordinal, raw_mapping in enumerate(mappings):
        mapping_label = f"{label} payer {payer_ordinal}"
        mapping = _mapping(raw_mapping, mapping_label)
        payer_name = _required_text(mapping.get("payer_name"), f"{mapping_label} payer_name")
        plan_name = _required_text(mapping.get("plan_name"), f"{mapping_label} plan_name")
        mapping_description = _required_text(
            mapping.get("description"),
            f"{mapping_label} description",
        )
        modifier_payer_id = _identifier(
            context.source_file_id, "modifier-payer", ordinal, payer_ordinal
        )
        spool.write(
            "stg_modifier_payer",
            (
                *_base(context),
                modifier_id,
                modifier_payer_id,
                payer_ordinal,
                payer_name,
                plan_name,
                _payer_plan_key(payer_name, plan_name),
                mapping_description,
            ),
        )
    return len(mappings)


def spool_hospital_file(
    source: Path,
    spool_dir: Path,
    context: NormalizeContext,
) -> tuple[NormalizedCounts, dict[str, Path]]:
    """Normalize ``source`` into typed spool files without accumulating source rows."""
    stats = StreamStats()
    counts = _Counts()
    with ExitStack() as stack:
        spool = _Spool(spool_dir, stack)
        source_handle = stack.enter_context(source.open("rb"))
        for entry in stream_array_entries(source_handle, ARRAY_KEY, stats=stats):
            codes, groups, payers, modifiers = _write_item(spool, context, entry)
            counts.items += 1
            counts.codes += codes
            counts.charge_groups += groups
            counts.payer_rates += payers
            counts.charge_modifiers += modifiers
        modifier_stats = StreamStats()
        modifier_handle = stack.enter_context(source.open("rb"))
        for entry in stream_array_entries(
            modifier_handle,
            MODIFIER_ARRAY_KEY,
            stats=modifier_stats,
            required=False,
        ):
            counts.modifiers += 1
            counts.modifier_payer_mappings += _write_modifier(spool, context, entry)
        paths = dict(spool.paths)
    problems = [*stats.problems, *modifier_stats.problems]
    if problems:
        raise NormalizationError("; ".join(problems))
    return NormalizedCounts(
        source_bytes_read=stats.bytes_read,
        items=counts.items,
        codes=counts.codes,
        charge_groups=counts.charge_groups,
        payer_rates=counts.payer_rates,
        modifiers=counts.modifiers,
        modifier_payer_mappings=counts.modifier_payer_mappings,
        charge_modifiers=counts.charge_modifiers,
        had_bom=stats.had_bom,
    ), paths


def spool_sizes(paths: Mapping[str, Path]) -> dict[str, int]:
    """Return measured spool bytes by model after all handles have been closed."""
    return {name: path.stat().st_size for name, path in paths.items()}
