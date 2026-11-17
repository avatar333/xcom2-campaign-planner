# ADR-0016: Pillow for image validation, re-encoding and asset storage

## Status
Accepted (Phase 3 implementation)

## Context
`ARCHITECTURE.md` §8 and §9 require uploaded images to be validated by content-sniffing (not just extension), size-capped, and re-encoded (to strip embedded metadata/EXIF and rule out polyglot files), per `FR-AST-2` and the security baseline. `ADR-0011` left the exact library unspecified.

## Decision
Use **Pillow** (`PIL`) for all image handling in `app/assets/storage.py`:
- `Image.open(...).verify()` followed by a fresh `Image.open(...)` re-decode confirms the bytes are a genuine, fully-decodable raster image (not a renamed script or truncated/corrupt file) before anything is written to disk or the database.
- Only `PNG`, `JPEG`, `WEBP`, `GIF` (Pillow's own detected `format`, not the client-supplied `Content-Type` or filename extension) are accepted — this directly implements `FR-AST-2`'s format allow-list.
- Every accepted image is re-encoded (JPEG/WebP/PNG re-save, or verified-and-passed-through for animated GIF, since re-encoding would collapse the animation) and resized if any dimension exceeds `MAX_DIMENSION` (2048px) before storage — this strips embedded metadata/EXIF and bounds page weight.
- The post-re-encode sha256 hash is what determines the content-addressed storage path (`ADR-0006`), so the stored bytes are always the sanitized version, never the raw upload.
- Filenames are never derived from user input — the hash is the only input to the storage path, which also rules out path traversal (`app/assets/storage.py:write_to_disk`, `read_from_disk` additionally double-checks the resolved path stays under the assets root).

## Consequences
- One new runtime dependency (`Pillow>=10.0,<12.0`), added to `pyproject.toml`/`requirements.txt`.
- Image validation is a pure function (`validate_and_reencode_image`) that raises `AssetValidationError` with a human-readable message on any problem, making it trivially unit-testable without Flask/DB (`tests/test_asset_storage.py`).
- Thumbnailing and re-encoding happen synchronously in the request (`ARCHITECTURE.md` §1: "no background workers in v1"), acceptable at this app's scale.

## Alternatives considered
- `imghdr` (stdlib, now removed in Python 3.13) for sniffing only, without re-encoding: rejected — detects format but does not strip embedded payloads/metadata, and stdlib `imghdr` was deprecated/removed, making it a poor long-term choice anyway.
- Shelling out to ImageMagick: rejected — adds an external binary dependency where a pure-Python library (Pillow) is sufficient and keeps the "no external services" offline-first principle (`PRODUCT_SPEC.md` §2) simpler to reason about.
