"""Catalog interchange format package (FR-SRC-7/8, ARCHITECTURE.md §5 seam).

This is a *leaner*, user-facing JSON/CSV import-export format for ad hoc
backup/sharing of catalog data edited through the UI -- distinct from (but
structurally compatible with) the richer `content/packs/<slug>/` pack
format described in `ARCHITECTURE.md` §5, which is reserved for the
curated base/WotC/LWotC data a future phase will ship. Both describe the
same underlying tables; this one is simpler and always validates with a
dry-run + conflict report before anything is written (never a silent
overwrite), per the Phase 3 brief.
"""
