"""The catalog interchange document shape + its JSON (de)serialization
(FR-SRC-7/8).

A deliberately simple, versioned format -- not a full copy of the richer
pack format in `ARCHITECTURE.md` §5 (which is for curated, officially
shipped content), but structurally compatible with it (same identity rule:
objects/relationships are addressed by `(source_slug, template_key)`, never
a raw database id, so a document is portable between databases).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

FORMAT_NAME = "xcom2-campaign-planner.catalog-export"
FORMAT_VERSION = 1


class InterchangeFormatError(ValueError):
    """Raised when a document's top-level shape/version is not understood."""


@dataclass
class SourceRef:
    slug: str
    name: str | None = None
    kind: str | None = None
    version: str | None = None
    steam_workshop_id: str | None = None
    description: str | None = None
    license: str | None = None
    attribution_text: str | None = None
    homepage_url: str | None = None


@dataclass
class ObjectTypeRef:
    slug: str
    name: str | None = None
    description: str | None = None


@dataclass
class ImageRef:
    source_url: str | None = None
    license: str | None = None
    attribution_text: str | None = None


@dataclass
class ObjectRecord:
    source_slug: str
    template_key: str
    type_slug: str
    name: str
    slug: str | None = None
    short_description: str | None = None
    long_description: str | None = None
    tier: int | None = None
    tags: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    is_user_authored: bool = False
    image: ImageRef | None = None


@dataclass
class RelationshipRecord:
    type_slug: str
    source_slug: str
    source_template_key: str
    target_slug: str
    target_template_key: str
    group_key: str | None = None
    group_logic: str | None = None
    amount: float | None = None
    unit: str | None = None


@dataclass
class CatalogDocument:
    format: str = FORMAT_NAME
    version: int = FORMAT_VERSION
    exported_at: str | None = None
    content_sources: list[SourceRef] = field(default_factory=list)
    object_types: list[ObjectTypeRef] = field(default_factory=list)
    objects: list[ObjectRecord] = field(default_factory=list)
    relationships: list[RelationshipRecord] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def to_json(document: CatalogDocument) -> str:
    payload = {
        "format": document.format,
        "version": document.version,
        "exported_at": document.exported_at or _now_iso(),
        "content_sources": [vars(s) for s in document.content_sources],
        "object_types": [vars(t) for t in document.object_types],
        "objects": [
            {**{k: v for k, v in vars(o).items() if k != "image"}, "image": vars(o.image) if o.image else None}
            for o in document.objects
        ],
        "relationships": [vars(r) for r in document.relationships],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def from_json(raw: str) -> CatalogDocument:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise InterchangeFormatError(f"Not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise InterchangeFormatError("Document must be a JSON object.")

    fmt = data.get("format")
    if fmt != FORMAT_NAME:
        raise InterchangeFormatError(
            f"Unrecognized format '{fmt}'; expected '{FORMAT_NAME}'."
        )

    version = data.get("version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise InterchangeFormatError(
            f"Unsupported document version '{version}'; this app understands up to version {FORMAT_VERSION}."
        )

    try:
        content_sources = [SourceRef(**s) for s in data.get("content_sources", [])]
        object_types = [ObjectTypeRef(**t) for t in data.get("object_types", [])]
        objects = []
        for o in data.get("objects", []):
            image_data = o.pop("image", None)
            image = ImageRef(**image_data) if image_data else None
            objects.append(ObjectRecord(**o, image=image))
        relationships = [RelationshipRecord(**r) for r in data.get("relationships", [])]
    except TypeError as exc:
        raise InterchangeFormatError(f"Malformed document entry: {exc}") from exc

    return CatalogDocument(
        format=fmt,
        version=version,
        exported_at=data.get("exported_at"),
        content_sources=content_sources,
        object_types=object_types,
        objects=objects,
        relationships=relationships,
    )
