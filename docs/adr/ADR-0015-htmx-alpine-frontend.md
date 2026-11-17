# ADR-0015: HTMX + Alpine.js, vendored locally, for progressive enhancement

## Status
Accepted (Phase 2 implementation)

## Context
`ADR-0011` anticipated "a small, optional bundler-free component layer (e.g. Alpine.js or htmx — decided at implementation time, recorded as an ADR update if adopted)" for drawers/modals/partial updates, while keeping the server as the sole source of business logic. The Phase 2 brief explicitly asks for this combination. `ADR-0006`/`NFR-4` require the app to work fully offline with no CDN dependency at runtime.

## Decision
Adopt **htmx** (partial-page swaps for modal/drawer workflows, campaign-switch context updates, and form submissions without full-page reloads) and **Alpine.js** (small client-side state: toggling a modal's open/closed class, dropdown menus) as the two JS dependencies, on top of hand-written CSS (no CSS framework). Both libraries are **downloaded once and vendored** into `app/static/js/vendor/htmx.min.js` and `app/static/js/vendor/alpinejs.min.js`, committed to the repository and served from `/static/js/vendor/...` — never loaded from a CDN `<script src="https://...">`, which would violate `NFR-4`/`ADR-0006`'s offline-first requirement and the CSP in `ARCHITECTURE.md` §9 (`script-src 'self'`). A thin `app/static/js/app.js` wires any bespoke behaviour (e.g. closing a modal on `Escape`, confirm-before-delete) using plain DOM APIs — no bundler, no build step, consistent with `ADR-0011`.

Both libraries are used strictly as progressive enhancement: every screen built in this phase is fully navigable and usable with JavaScript disabled (full-page Jinja-rendered routes exist for every mutating action); htmx attributes (`hx-get`, `hx-post`, `hx-target`, `hx-swap`) point at the *same* Flask routes that also render full pages, differentiated by the `HX-Request` header server-side (fragment template vs. full-page template), so there is exactly one route/validation/service path per action, never a duplicated "API variant" of the business logic.

## Consequences
- Modal dialogs (create/edit/delete campaign) and the campaign switcher can update in place without a full navigation, improving the "polished" feel requested, while the underlying forms still work as plain HTML forms if JS is unavailable.
- No new Python dependency; no `npm` build pipeline; `app/static/js/vendor/*` are checked in as static assets (their own upstream licence, both MIT, noted in a `app/static/js/vendor/LICENSES.md` file).
- Future phases (drawers for object detail, requirement tree) reuse the same htmx/Alpine pattern already established here rather than introducing a second client-side approach.

## Alternatives considered
- CDN-hosted htmx/Alpine (`<script src="https://unpkg.com/...">`): rejected outright — violates `NFR-4` (fully offline) and the CSP's `script-src 'self'`.
- A full SPA framework (React/Vue): rejected per the brief's explicit preference and `ADR-0011`'s existing rejection of a client-side build pipeline.
- No JS at all (pure server-rendered, full page reloads only): considered; rejected as insufficiently "polished" for modal/drawer workflows explicitly requested, when htmx achieves this with negligible added complexity and no business-logic duplication.
