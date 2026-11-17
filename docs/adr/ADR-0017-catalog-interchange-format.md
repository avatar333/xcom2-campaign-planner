# ADR-0017: A lean, user-facing catalog interchange format, separate from the curated pack format

## Status
Accepted (Phase 3 implementation)

## Context
`ARCHITECTURE.md` §5 defines a rich content-pack format (`content/packs/<slug>/{pack.json, objects.json, relationships.json, assets/}`) intended for curated, officially-shipped base/WotC/LWotC data, with full dependency declarations (`requires`/`extends`/`conflicts`), override/patch semantics, and a filesystem-directory shape. The Phase 3 brief asks for a simpler "initial safe import/export format for catalog data" usable from the UI today (upload/paste a single JSON document, validate, review conflicts, confirm) — a materially different use case (ad hoc backup/sharing of whatever a user has edited through the UI) from "ship a curated pack directory".

## Decision
Introduce a second, simpler **catalog interchange document** format (`app/importer/document.py`): a single versioned JSON document (`{"format": "xcom2-campaign-planner.catalog-export", "version": 1, "content_sources": [...], "object_types": [...], "objects": [...], "relationships": [...]}`) produced by `GET /catalog/export` and consumed by `POST /catalog/import`. It is structurally compatible with the richer pack format's identity rules — objects and relationships are always addressed by `(source_slug, template_key)` pairs, never raw database ids, so a document authored on one installation imports correctly into another — but it has no directory structure, no bundled binary assets, and no `requires`/`extends`/`conflicts` dependency graph; those richer mod-dependency semantics remain exclusive to the `content/packs/` format for curated data (future phase).

Import always runs `app/importer/validator.py`'s dry-run `validate_document()` first (producing a `ValidationReport` of errors/warnings and new/conflicting counts) and only commits via `app/services/catalog_import.py:import_document()` if there are zero errors and the user has explicitly confirmed after reviewing the report — matching the brief's "preview/validation, conflict detection, clear error reporting" requirement. Conflicting objects/relationships (same `(source_slug, template_key)` or same relationship natural key already present) are **skipped, never overwritten** — satisfied by both the validator (reports them as warnings) and the importer (re-checks and skips at commit time, defending against a race between dry-run and commit).

## Consequences
- Two JSON formats now exist for two different jobs: `content/packs/` (future curated distribution, with mod dependency/precedence semantics) and the catalog interchange document (today's ad hoc backup/sharing of a user's own edited catalog, FR-SRC-7/8). Both are documented so a future session does not conflate them or try to collapse them into one.
- The interchange document's `version` field (currently `1`) allows the format to evolve; `from_json()` rejects any version greater than what this app build understands, with a clear error rather than silent misbehaviour.
- CSV support was deliberately not built this phase (the brief marks it optional, "may optionally be supported"); the JSON format alone already satisfies the mandatory "prefer JSON for the canonical rich format" requirement, and the richer relational structure (multi-valued tags, nested attributes, relationships) does not map cleanly onto flat CSV rows without a lossy, bespoke convention that would need its own design pass — deferred as a clearly-scoped future addition rather than rushed in.

## Alternatives considered
- Reusing `content/packs/` format verbatim for UI export/import: rejected — that format assumes a directory on disk with a bundled `assets/` folder, which does not match "upload or paste a JSON blob through the browser"; forcing a zip/directory upload pipeline into this phase was unnecessary complexity for what the brief asks for now.
- Allowing import to overwrite existing objects by default: rejected outright — the brief explicitly requires "where conflicts occur, do not silently overwrite existing records."
