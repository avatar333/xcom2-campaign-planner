# Architecture Decision Records

Format: lightweight [MADR](https://adr.github.io/)-style — Context, Decision, Consequences, Alternatives considered. Numbered sequentially, never renumbered. Status: `Accepted` unless marked `Superseded by ADR-xxxx`.

| ID | Title | Status |
|---|---|---|
| [ADR-0001](ADR-0001-spec-driven-development.md) | Spec-Driven Development workflow | Accepted |
| [ADR-0002](ADR-0002-catalog-campaign-separation.md) | Catalog data vs. campaign state are separate, never merged | Accepted |
| [ADR-0003](ADR-0003-data-driven-object-types.md) | Data-driven object types (no schema change per category) | Accepted |
| [ADR-0004](ADR-0004-generic-relationship-model.md) | Generic polymorphic relationship model | Accepted |
| [ADR-0005](ADR-0005-content-pack-format-and-import.md) | JSON content-pack format with idempotent, checksum-tracked import | Accepted |
| [ADR-0006](ADR-0006-local-asset-storage.md) | All runtime images are served from local storage; no hot-linking | Accepted |
| [ADR-0007](ADR-0007-forward-only-sql-migrations.md) | Forward-only, numbered, hand-written SQL migrations | Superseded by ADR-0014 |
| [ADR-0008](ADR-0008-asset-redistribution-policy.md) | No copyrighted game assets bundled in the repository | Accepted |
| [ADR-0009](ADR-0009-facts-vs-strategy-profiles.md) | Game facts and strategy recommendations are modeled and stored separately | Accepted |
| [ADR-0010](ADR-0010-campaign-isolation.md) | Campaign isolation strategy (FK ownership, no shared mutable rows) | Accepted |
| [ADR-0011](ADR-0011-technology-stack.md) | Technology stack: Flask, stdlib `sqlite3`, server-rendered Jinja + progressive JS | Accepted |
| [ADR-0012](ADR-0012-attributes-eav-vs-json.md) | Typed attributes via validated JSON column, not full EAV tables | Accepted |
| [ADR-0013](ADR-0013-source-precedence-model.md) | Source precedence and override/patch resolution algorithm | Accepted |
| [ADR-0014](ADR-0014-sqlalchemy-and-alembic.md) | SQLAlchemy ORM + Alembic-managed migrations | Accepted — supersedes ADR-0007, amends ADR-0011 |
| [ADR-0015](ADR-0015-htmx-alpine-frontend.md) | HTMX + Alpine.js, vendored locally, for progressive enhancement | Accepted — fulfils the option left open in ADR-0011 |
| [ADR-0016](ADR-0016-pillow-image-handling.md) | Pillow for image validation, re-encoding and asset storage | Accepted (Phase 3) |
| [ADR-0017](ADR-0017-catalog-interchange-format.md) | A lean, user-facing catalog interchange format, separate from the curated pack format | Accepted (Phase 3) |
| [ADR-0018](ADR-0018-compound-prerequisite-groups.md) | Compound (nested AND/OR) prerequisite expression tree | Accepted (Phase 4) |
| [ADR-0019](ADR-0019-content-source-revision-pinning.md) | Content-source revision pinning and provenance fields | Accepted (Phase 4) |
| [ADR-0020](ADR-0020-avenger-grid-model.md) | Campaign Avenger grid: separate room state, facility assignment and build lifecycle | Accepted (Phase 5) |
| [ADR-0021](ADR-0021-research-dependency-graph.md) | Research queue, completion tracking and the generic dependency graph | Accepted (Phase 6) |
