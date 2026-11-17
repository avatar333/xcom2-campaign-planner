# ADR-0008: No copyrighted game assets bundled in the repository

## Status
Accepted

## Context
XCOM 2 artwork (icons, portraits, splash art) is Firaxis/2K-copyrighted; UFOpaedia/wiki images carry their own licensing; Workshop mod assets belong to their authors. The brief explicitly forbids redistributing such assets in the repo unless rights are clear.

## Decision
The git repository and any curated "base/DLC/LWotC" content packs shipped with the app contain **zero** binary game-derived images. Seeded packs ship only: (a) generic placeholder/fallback SVG glyphs per object category, authored in-house (MIT/CC0), and (b) factual text data (names, template IDs, numeric stats, relationships — facts are not copyrightable the way art is). `assets.redistribution_allowed` must be explicitly `1` for any asset to be considered for bundling in a *shareable* exported pack (`FR-SRC-8`); the exporter refuses to include assets where this flag is unset or `0`. For a user's own local instance, the user may legitimately populate images by: uploading files they own/extracted from their own game install, or using the explicit "fetch from URL" action against a source *they* choose — both are the user's own action on their own machine, outside what the project repository or any shared pack distributes. CI includes a repository check (`scripts/check_no_binary_assets.py`) failing the build if an image file appears under `content/packs/**/assets/` without a matching `license: public-domain-or-own-authorship` entry in that pack's manifest.

## Consequences
- The project repo and any packs the maintainer distributes stay unambiguously clear of 2K/Firaxis/third-party copyrighted art.
- Users are responsible for their own locally-sourced images; the app only ever stores what the user explicitly uploaded or fetched, never auto-scrapes.
- UFOpaedia/wiki text (names, numbers) is used as *factual reference* to author pack data (see `DATA_SOURCING.md`), not copied verbatim as prose; long descriptions default to short, originally-worded summaries.

## Alternatives considered
- Bundling wiki-sourced images "for convenience": rejected outright, directly against the stated constraint and real licensing risk.
- An opt-in "download art pack" fetched from a project-controlled CDN at install time: deferred/non-goal for v1; same licensing risk just moved behind an install step, revisit only with confirmed redistribution rights.
