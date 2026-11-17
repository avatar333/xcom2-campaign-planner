"""Validate catalog-object `attributes` JSON against a type's
`catalog_attribute_definitions` (ADR-0012, FR-CAT-7).

Pure functions: definitions and candidate values are passed in as plain
dicts/lists (never ORM instances), so this is unit-testable without a DB
and reusable by both the manual "add object" form and any future importer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from numbers import Number
from typing import Any

ATTRIBUTE_DATA_TYPES = ("string", "integer", "number", "boolean", "enum", "text")


@dataclass(frozen=True)
class AttributeDefinition:
    key: str
    data_type: str
    enum_values: list[str] | None = None
    unit: str | None = None
    is_required: bool = False
    default_value: Any = None


def _coerce(data_type: str, value: Any, *, key: str, enum_values: list[str] | None) -> Any:
    if data_type == "string" or data_type == "text":
        if not isinstance(value, str):
            raise ValueError(f"'{key}' must be text.")
        return value
    if data_type == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, Number)):
            raise ValueError(f"'{key}' must be an integer.")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"'{key}' must be an integer.")
        return int(value)
    if data_type == "number":
        if isinstance(value, bool) or not isinstance(value, Number):
            raise ValueError(f"'{key}' must be a number.")
        return float(value)
    if data_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"'{key}' must be true/false.")
        return value
    if data_type == "enum":
        if value not in (enum_values or []):
            allowed = ", ".join(enum_values or [])
            raise ValueError(f"'{key}' must be one of: {allowed}.")
        return value
    raise ValueError(f"Unknown attribute data type '{data_type}' for '{key}'.")


def validate_attributes(
    definitions: list[AttributeDefinition], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Validate+coerce `candidate` against `definitions`.

    Unknown keys (not declared by any definition) are rejected -- the
    "attributes is not a dumping ground" rule from ADR-0012. Returns the
    coerced, validated dict ready to be JSON-serialized onto the model.
    Raises `ValueError` with a human-readable message on the first problem.
    """
    by_key = {d.key: d for d in definitions}

    unknown = set(candidate.keys()) - set(by_key.keys())
    if unknown:
        raise ValueError(f"Unknown attribute(s): {', '.join(sorted(unknown))}.")

    result: dict[str, Any] = {}
    for definition in definitions:
        if definition.key in candidate:
            raw_value = candidate[definition.key]
            if raw_value is None or raw_value == "":
                if definition.is_required:
                    raise ValueError(f"'{definition.key}' is required.")
                continue
            result[definition.key] = _coerce(
                definition.data_type,
                raw_value,
                key=definition.key,
                enum_values=definition.enum_values,
            )
        elif definition.is_required and definition.default_value is None:
            raise ValueError(f"'{definition.key}' is required.")
        elif definition.default_value is not None:
            result[definition.key] = definition.default_value

    return result


def parse_attributes_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Attributes must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Attributes must be a JSON object.")  # noqa: TRY004 -- ValueError is this module's uniform validation-error type, not a type-check failure
    return data


def dump_attributes_json(attributes: dict[str, Any]) -> str:
    return json.dumps(attributes, sort_keys=True)
