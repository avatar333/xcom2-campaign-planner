# ADR-0019: Content-source revision pinning and provenance fields

## Status
Accepted (Phase 4)

## Context
The Phase 4 brief requires that curated datasets "identify the source version/revision from which it was created," explicitly calling out Git repositories such as LWotC: "record where practical: repository, branch/tag/release, commit SHA or other immutable revision, ingestion date. Do not assume that `master` will always represent the same game balance." `content_sources` already has `version` (a free-form string) and `imported_at`, but nothing captures *which exact upstream revision* a curated pack's facts were transcribed from, which is required to answer "what changed" on a future refresh.

## Decision
Add three nullable columns to `content_sources`: `source_repository` (e.g. `github.com/long-war-2/lwotc`), `source_ref` (the pinned branch/tag/release name, e.g. `v1.1.0`), `source_commit_sha` (the immutable commit SHA at that ref, e.g. `4872d8e27910e186fac2a67201593fff7c2df7e2`). These are populated by the curated pack's `pack.json` manifest (`docs/DATA_INGESTION.md`) at import time and are purely informational metadata — they do not participate in identity/precedence (`ADR-0013` is unchanged) and are nullable because they only apply to sources curated from an external repository (a Steam Workshop mod or hand-authored user content has no meaningful "commit SHA").

`imported_at` (already existing) continues to serve as the "ingestion date." A future refresh command (`docs/DATA_INGESTION.md` "refresh" command) compares the stored `source_commit_sha` against the latest commit at `source_ref` to report "N commits behind" before re-ingesting, and a human author re-curates the pack data against the new commit and updates `pack.json` accordingly — this ADR does not attempt automatic diffing of upstream `.ini`/`.uc` source against the curated JSON, which the brief explicitly discourages ("do not create a brittle one-off regex dump").

## Consequences
- Every curated pack's provenance is inspectable directly from the `content_sources` row (`docs/DATA_SOURCING.md` documents, per source, exactly what `source_repository`/`source_ref`/`source_commit_sha` were used to author the shipped pack data).
- Re-importing an unchanged pack remains a no-op via the existing `import_checksum` mechanism (`ADR-0005`); these new columns are metadata about *why* the checksum is what it is, not a second idempotency key.
- User-authored and Workshop-mod sources simply leave these columns NULL; no behavioural change for them.

## Alternatives considered
- Storing provenance only in `pack.json`/`DATA_SOURCING.md` prose, never in the database: rejected — the brief asks the application to "eventually be able to explain why a particular definition is active," which requires the provenance to be queryable from the running app (e.g. a future "view source" UI action on a content source), not just documented in a file the app never reads.
- A separate `content_source_provenance` table: rejected as unnecessary normalization — these are 1:1, rarely-queried metadata fields on a source, not a repeating group; three nullable columns on the existing row is simpler and matches how `import_checksum`/`imported_at` already work.
