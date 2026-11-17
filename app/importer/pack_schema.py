"""Canonical curated content-pack format: data shape + loader
(`ARCHITECTURE.md` §5, Phase 4 "Primary objective").

Distinct from `app/importer/document.py` (the Phase 3 ad hoc UI
import/export interchange format, `ADR-0017`) -- this module is for the
curated, maintainer-authored `content/packs/<slug>/` directories that ship
factual XCOM 2/WotC/LWotC game data (`docs/DATA_SOURCING.md`,
`docs/DATA_INGESTION.md`).

Pack directory layout:
    content/packs/<slug>/
      pack.json                   required: manifest (slug, name, kind, version, provenance, requires/extends/conflicts)
      object_types.json           optional: new catalog_object_types + attribute definitions
      relationship_types.json     optional: new catalog_relationship_types
      objects.json                required: catalog objects (array)
      relationships.json          optional: flat AND/OR relationships (array)
      prerequisite_groups.json    optional: compound nested AND/OR trees (ADR-0018)
      stat_profiles.json          optional: soldier stat profiles (ADR per brief)
      asset_manifest.json         optional: asset provenance records (brief "asset manifest")
      assets/                     optional: local image files, only ever own-authored placeholders (ADR-0008)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PACK_SCHEMA_VERSION = 1


class PackFormatError(ValueError):
    """Raised when a pack directory's JSON files are structurally invalid."""


@dataclass
class PackRelation:
    relation: str  # "requires" | "conflicts" | "extends"
    source_slug: str


@dataclass
class PackManifest:
    schema_version: int
    slug: str
    name: str
    kind: str
    version: str
    steam_workshop_id: str | None = None
    description: str | None = None
    license: str | None = None
    attribution_text: str | None = None
    homepage_url: str | None = None
    requires: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    source_repository: str | None = None
    source_ref: str | None = None
    source_commit_sha: str | None = None
    ingestion_notes: str | None = None


@dataclass
class PackObjectTypeDef:
    slug: str
    name: str
    description: str | None = None
    attributes: list[dict] = field(default_factory=list)  # [{key, data_type, unit, is_required, default_value, display_hint, enum_values}]


@dataclass
class PackRelationshipTypeDef:
    slug: str
    name: str
    inverse_slug: str | None = None
    is_quantified: bool = False


@dataclass
class PackObjectImageRef:
    local_path: str | None = None
    source_url: str | None = None
    source_name: str | None = None
    original_filename: str | None = None
    license: str | None = None
    attribution_text: str | None = None
    redistribution_allowed: bool = False


@dataclass
class PackObjectRecord:
    template_key: str
    type_slug: str
    name: str
    slug: str | None = None
    short_description: str | None = None
    long_description: str | None = None
    tier: int | None = None
    tags: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    overrides: tuple[str, str] | None = None  # (source_slug, template_key)
    replaces: tuple[str, str] | None = None
    patch: dict | None = None
    image: PackObjectImageRef | None = None
    is_retired: bool = False


@dataclass
class PackRelationshipRecord:
    type_slug: str
    source: tuple[str, str]  # (source_slug, template_key)
    target: tuple[str, str]
    group_key: str | None = None
    group_logic: str | None = None
    amount: float | None = None
    unit: str | None = None


@dataclass
class PackPrerequisiteGroupNode:
    """One node of a compound prerequisite tree (ADR-0018)."""

    logic: str  # "AND" | "OR"
    members: list[PackPrerequisiteGroupNode | tuple[str, str]] = field(default_factory=list)
    # a member is either a nested PackPrerequisiteGroupNode, or a leaf
    # (source_slug, template_key) tuple referencing a catalog object.


@dataclass
class PackPrerequisiteGroupRecord:
    target: tuple[str, str]
    relationship_type_slug: str
    root: PackPrerequisiteGroupNode


@dataclass
class PackStatProfileRecord:
    target: tuple[str, str]
    stat_key: str
    difficulty: str | None
    stat_min: float | None
    stat_default: float | None
    stat_max: float | None
    is_randomized: bool


@dataclass
class PackAssetManifestEntry:
    target: tuple[str, str]
    role: str
    local_path: str | None = None
    source_url: str | None = None
    source_name: str | None = None
    original_filename: str | None = None
    license: str | None = None
    attribution_text: str | None = None
    redistribution_allowed: bool = False
    import_status: str = "pending"


@dataclass
class PackDocument:
    manifest: PackManifest
    object_types: list[PackObjectTypeDef] = field(default_factory=list)
    relationship_types: list[PackRelationshipTypeDef] = field(default_factory=list)
    objects: list[PackObjectRecord] = field(default_factory=list)
    relationships: list[PackRelationshipRecord] = field(default_factory=list)
    prerequisite_groups: list[PackPrerequisiteGroupRecord] = field(default_factory=list)
    stat_profiles: list[PackStatProfileRecord] = field(default_factory=list)
    asset_manifest: list[PackAssetManifestEntry] = field(default_factory=list)
    pack_dir: Path | None = None


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackFormatError(f"Could not read/parse {path}: {exc}") from exc


def _as_pair(value, *, context: str) -> tuple[str, str]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(isinstance(v, str) for v in value)
    ):
        raise PackFormatError(f"{context} must be a [source_slug, template_key] pair, got {value!r}.")
    return (value[0], value[1])


def _load_group_node(data: dict, *, context: str) -> PackPrerequisiteGroupNode:
    if not isinstance(data, dict) or "logic" not in data or "members" not in data:
        raise PackFormatError(f"{context}: prerequisite group node needs 'logic' and 'members'.")
    logic = data["logic"]
    if logic not in ("AND", "OR"):
        raise PackFormatError(f"{context}: logic must be 'AND' or 'OR', got {logic!r}.")
    members = []
    for i, member in enumerate(data["members"]):
        member_context = f"{context}.members[{i}]"
        if isinstance(member, dict) and "logic" in member:
            members.append(_load_group_node(member, context=member_context))
        else:
            members.append(_as_pair(member, context=member_context))
    return PackPrerequisiteGroupNode(logic=logic, members=members)


def load_pack(pack_dir: str | Path) -> PackDocument:
    """Load a curated content-pack directory into a `PackDocument`.

    Raises `PackFormatError` for any structural problem. This function does
    not touch the database -- see `app/importer/pack_validator.py` for
    dry-run validation against existing data and
    `app/services/content_import.py` for the actual import/commit.
    """
    root = Path(pack_dir)
    manifest_path = root / "pack.json"
    if not manifest_path.is_file():
        raise PackFormatError(f"Missing required pack.json in {root}.")

    manifest_data = _read_json(manifest_path)
    if not isinstance(manifest_data, dict):
        raise PackFormatError("pack.json must be a JSON object.")

    required_manifest_keys = {"slug", "name", "kind", "version"}
    missing = required_manifest_keys - manifest_data.keys()
    if missing:
        raise PackFormatError(f"pack.json is missing required keys: {sorted(missing)}.")

    schema_version = manifest_data.get("schema_version", PACK_SCHEMA_VERSION)
    if schema_version > PACK_SCHEMA_VERSION:
        raise PackFormatError(
            f"pack.json schema_version {schema_version} is newer than this app understands "
            f"(max {PACK_SCHEMA_VERSION})."
        )

    manifest = PackManifest(
        schema_version=schema_version,
        slug=manifest_data["slug"],
        name=manifest_data["name"],
        kind=manifest_data["kind"],
        version=manifest_data["version"],
        steam_workshop_id=manifest_data.get("steam_workshop_id"),
        description=manifest_data.get("description"),
        license=manifest_data.get("license"),
        attribution_text=manifest_data.get("attribution_text"),
        homepage_url=manifest_data.get("homepage_url"),
        requires=list(manifest_data.get("requires", [])),
        extends=list(manifest_data.get("extends", [])),
        conflicts=list(manifest_data.get("conflicts", [])),
        source_repository=manifest_data.get("source_repository"),
        source_ref=manifest_data.get("source_ref"),
        source_commit_sha=manifest_data.get("source_commit_sha"),
        ingestion_notes=manifest_data.get("ingestion_notes"),
    )

    object_types: list[PackObjectTypeDef] = []
    object_types_path = root / "object_types.json"
    if object_types_path.is_file():
        raw = _read_json(object_types_path)
        if not isinstance(raw, list):
            raise PackFormatError("object_types.json must be a JSON array.")
        for i, entry in enumerate(raw):
            if "slug" not in entry or "name" not in entry:
                raise PackFormatError(f"object_types.json[{i}] needs 'slug' and 'name'.")
            object_types.append(
                PackObjectTypeDef(
                    slug=entry["slug"],
                    name=entry["name"],
                    description=entry.get("description"),
                    attributes=list(entry.get("attributes", [])),
                )
            )

    relationship_types: list[PackRelationshipTypeDef] = []
    relationship_types_path = root / "relationship_types.json"
    if relationship_types_path.is_file():
        raw = _read_json(relationship_types_path)
        if not isinstance(raw, list):
            raise PackFormatError("relationship_types.json must be a JSON array.")
        for i, entry in enumerate(raw):
            if "slug" not in entry or "name" not in entry:
                raise PackFormatError(f"relationship_types.json[{i}] needs 'slug' and 'name'.")
            relationship_types.append(
                PackRelationshipTypeDef(
                    slug=entry["slug"],
                    name=entry["name"],
                    inverse_slug=entry.get("inverse_slug"),
                    is_quantified=bool(entry.get("is_quantified", False)),
                )
            )

    objects: list[PackObjectRecord] = []
    objects_path = root / "objects.json"
    if objects_path.is_file():
        raw = _read_json(objects_path)
        if not isinstance(raw, list):
            raise PackFormatError("objects.json must be a JSON array.")
        for i, entry in enumerate(raw):
            context = f"objects.json[{i}]"
            for required_key in ("template_key", "type", "name"):
                if required_key not in entry:
                    raise PackFormatError(f"{context} is missing required key '{required_key}'.")

            image = None
            image_data = entry.get("image")
            if image_data:
                image = PackObjectImageRef(
                    local_path=image_data.get("local_path"),
                    source_url=image_data.get("source_url"),
                    source_name=image_data.get("source_name"),
                    original_filename=image_data.get("original_filename"),
                    license=image_data.get("license"),
                    attribution_text=image_data.get("attribution_text"),
                    redistribution_allowed=bool(image_data.get("redistribution_allowed", False)),
                )

            overrides = entry.get("overrides")
            replaces = entry.get("replaces")
            objects.append(
                PackObjectRecord(
                    template_key=entry["template_key"],
                    type_slug=entry["type"],
                    name=entry["name"],
                    slug=entry.get("slug"),
                    short_description=entry.get("short_description"),
                    long_description=entry.get("long_description"),
                    tier=entry.get("tier"),
                    tags=list(entry.get("tags", [])),
                    attributes=dict(entry.get("attributes", {})),
                    overrides=_as_pair(overrides, context=f"{context}.overrides") if overrides else None,
                    replaces=_as_pair(replaces, context=f"{context}.replaces") if replaces else None,
                    patch=entry.get("patch"),
                    image=image,
                    is_retired=bool(entry.get("is_retired", False)),
                )
            )
    else:
        raise PackFormatError(f"Missing required objects.json in {root}.")

    relationships: list[PackRelationshipRecord] = []
    relationships_path = root / "relationships.json"
    if relationships_path.is_file():
        raw = _read_json(relationships_path)
        if not isinstance(raw, list):
            raise PackFormatError("relationships.json must be a JSON array.")
        for i, entry in enumerate(raw):
            context = f"relationships.json[{i}]"
            for required_key in ("type", "from", "to"):
                if required_key not in entry:
                    raise PackFormatError(f"{context} is missing required key '{required_key}'.")
            relationships.append(
                PackRelationshipRecord(
                    type_slug=entry["type"],
                    source=_as_pair(entry["from"], context=f"{context}.from"),
                    target=_as_pair(entry["to"], context=f"{context}.to"),
                    group_key=entry.get("group_key"),
                    group_logic=entry.get("group_logic"),
                    amount=entry.get("amount"),
                    unit=entry.get("unit"),
                )
            )

    prerequisite_groups: list[PackPrerequisiteGroupRecord] = []
    groups_path = root / "prerequisite_groups.json"
    if groups_path.is_file():
        raw = _read_json(groups_path)
        if not isinstance(raw, list):
            raise PackFormatError("prerequisite_groups.json must be a JSON array.")
        for i, entry in enumerate(raw):
            context = f"prerequisite_groups.json[{i}]"
            for required_key in ("target", "relationship_type", "root"):
                if required_key not in entry:
                    raise PackFormatError(f"{context} is missing required key '{required_key}'.")
            prerequisite_groups.append(
                PackPrerequisiteGroupRecord(
                    target=_as_pair(entry["target"], context=f"{context}.target"),
                    relationship_type_slug=entry["relationship_type"],
                    root=_load_group_node(entry["root"], context=f"{context}.root"),
                )
            )

    stat_profiles: list[PackStatProfileRecord] = []
    stat_profiles_path = root / "stat_profiles.json"
    if stat_profiles_path.is_file():
        raw = _read_json(stat_profiles_path)
        if not isinstance(raw, list):
            raise PackFormatError("stat_profiles.json must be a JSON array.")
        for i, entry in enumerate(raw):
            context = f"stat_profiles.json[{i}]"
            for required_key in ("target", "stat_key"):
                if required_key not in entry:
                    raise PackFormatError(f"{context} is missing required key '{required_key}'.")
            stat_profiles.append(
                PackStatProfileRecord(
                    target=_as_pair(entry["target"], context=f"{context}.target"),
                    stat_key=entry["stat_key"],
                    difficulty=entry.get("difficulty"),
                    stat_min=entry.get("stat_min"),
                    stat_default=entry.get("stat_default"),
                    stat_max=entry.get("stat_max"),
                    is_randomized=bool(entry.get("is_randomized", False)),
                )
            )

    asset_manifest: list[PackAssetManifestEntry] = []
    asset_manifest_path = root / "asset_manifest.json"
    if asset_manifest_path.is_file():
        raw = _read_json(asset_manifest_path)
        if not isinstance(raw, list):
            raise PackFormatError("asset_manifest.json must be a JSON array.")
        for i, entry in enumerate(raw):
            context = f"asset_manifest.json[{i}]"
            for required_key in ("target", "role"):
                if required_key not in entry:
                    raise PackFormatError(f"{context} is missing required key '{required_key}'.")
            asset_manifest.append(
                PackAssetManifestEntry(
                    target=_as_pair(entry["target"], context=f"{context}.target"),
                    role=entry["role"],
                    local_path=entry.get("local_path"),
                    source_url=entry.get("source_url"),
                    source_name=entry.get("source_name"),
                    original_filename=entry.get("original_filename"),
                    license=entry.get("license"),
                    attribution_text=entry.get("attribution_text"),
                    redistribution_allowed=bool(entry.get("redistribution_allowed", False)),
                    import_status=entry.get("import_status", "pending"),
                )
            )

    return PackDocument(
        manifest=manifest,
        object_types=object_types,
        relationship_types=relationship_types,
        objects=objects,
        relationships=relationships,
        prerequisite_groups=prerequisite_groups,
        stat_profiles=stat_profiles,
        asset_manifest=asset_manifest,
        pack_dir=root,
    )


def compute_pack_checksum(pack_dir: str | Path) -> str:
    """SHA-256 of the pack's canonicalized JSON content (not file mtimes),
    for idempotent-import detection (`ADR-0005`, NFR-8)."""
    import hashlib

    root = Path(pack_dir)
    pieces = []
    for filename in (
        "pack.json",
        "object_types.json",
        "relationship_types.json",
        "objects.json",
        "relationships.json",
        "prerequisite_groups.json",
        "stat_profiles.json",
        "asset_manifest.json",
    ):
        path = root / filename
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            pieces.append(filename + ":" + json.dumps(data, sort_keys=True))
    canonical = "\n".join(pieces)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
