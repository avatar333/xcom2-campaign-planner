# ADR-0006: All runtime images are served from local storage; no hot-linking

## Status
Accepted

## Context
The app must work fully offline (NFR-4) and must not become dependent on third-party hosts (UFOpaedia, wikis, Workshop CDNs) staying up or allowing hot-linking, which is often against their terms.

## Decision
`assets` table models every image as a local file under `instance/assets/<content-hash-prefix>/<content-hash>.<ext>` (content-addressed storage, dedupes identical files) plus metadata: `origin` (`upload`, `pack_bundled`, `fetched_url`, `generated_thumbnail`), `source_url` (nullable, informational only — never fetched at render time), `license`, `attribution`, `redistribution_allowed` (bool). `catalog_objects`/campaign entities reference assets via `object_assets (object_id, asset_id, role, sort_order)` where `role` ∈ `icon|portrait|banner|gallery`. The one user-triggered exception (`FR-AST-4`) is an explicit "fetch from URL" action that downloads once and stores the result as a normal local asset with `origin='fetched_url'`; the running UI templates only ever emit `/assets/<id>` URLs served from local disk, never a third-party URL. Every `catalog_object_type` declares a default fallback asset (seeded SVG glyph per category) so an object with no image still renders an icon + name (brief requirement).

## Consequences
- No `<img src="https://...">` ever appears in rendered HTML; CSP can disallow remote image origins entirely (NFR-7).
- Content-addressing means re-importing a pack whose assets are unchanged costs no disk I/O (hash match).
- Thumbnailing/re-encoding on upload (FR-AST-2) is mandatory to keep page weight predictable and to strip EXIF/metadata.

## Alternatives considered
- Hot-linking with a local cache-on-miss proxy: rejected — still a runtime network dependency and licensing grey area for redistribution-unclear assets (see ADR-0008); violates NFR-4 strictly.
- Storing images as DB BLOBs: rejected for a file-based asset model — SQLite can store blobs fine, but filesystem storage keeps the DB small/portable and allows the browser/webserver to serve files with normal caching headers; `assets.storage_path` is the only required column either way, so this can be revisited without a model change if needed.
