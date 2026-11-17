# ADR-0001: Spec-Driven Development workflow

## Status
Accepted

## Context
The project is built incrementally, likely across multiple AI-assisted coding sessions and/or contributors who do not share conversational context. XCOM 2's domain (content packs, mods, campaign isolation, extensible object types) is complex enough that undocumented assumptions will diverge quickly and cause rework or silent bugs (e.g. two sessions inventing two different "override" mechanisms).

## Decision
Every behavioural change is preceded by a documentation change under `/docs`. The documents in `/docs` are the source of truth:

- `PRODUCT_SPEC.md` — requirements (FR-*/NFR-*), success criteria, non-goals.
- `DOMAIN_MODEL.md` — ubiquitous language, entities, invariants.
- `ARCHITECTURE.md` — system structure, module boundaries, content-pack format, precedence algorithm.
- `DATABASE_SCHEMA.md` — authoritative relational schema (tables, keys, indexes, delete rules).
- `UI_UX_SPEC.md` — screens, navigation, components, interaction patterns.
- `IMPLEMENTATION_PLAN.md` — phased delivery plan and definition-of-done per phase.
- `adr/*` — individual architecture decisions with rationale and alternatives considered.
- `DATA_SOURCING.md` — where factual game data comes from and how it is verified.

A coding session (human or AI) must read the relevant docs before writing code, implement exactly what is specified, and if a gap or contradiction is found, fix the docs first (or record a new ADR), then implement. Tests are written against the documented behaviour, not against incidental implementation details.

## Consequences
- Slower first phase (this one), faster and safer subsequent phases — a new session can onboard from `/docs` alone.
- Docs must be kept in sync; PRs that change behaviour without a doc change are considered incomplete.
- Requirement IDs (`FR-*`, `NFR-*`) give tests and commits stable anchors to reference.

## Alternatives considered
- Code-first / tribal knowledge: rejected, too risky given multi-session AI development and the domain's complexity.
- Heavyweight formal spec (e.g. full OpenAPI + ERD tooling) upfront: rejected as premature for a single-user local app; Markdown + ADRs is sufficient and cheap to maintain.
