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

## Amendment (19/11/2026, bug fix 01): passing server data to Alpine components
Any component more than a trivial toggle is a named component registered in `app/static/js/*.js` with `Alpine.data("name", ...)` and referenced as `x-data="name"` / `x-data="name()"`. Server-side state reaches it **only as data**: serialised once with Jinja's `tojson` inside `<script type="application/json" id="...">`, and read with `JSON.parse(el.textContent)`. Per-element identity (slot type, index, object id) travels in `data-*` attributes and is read via `el.dataset`. Server values are never spliced into JavaScript source or into an `x-data`/`x-on`/`:attr` expression.

Why: `tojson` escapes `<`, `>`, `&` and `'` but **not `"`**, so it is only safe inside a `<script>` element. Used inside a double-quoted attribute (`x-data="{ items: {{ items | tojson }} }"`) the first `"` ends the attribute and the remainder of the component source is rendered as page text (this is exactly how the Edit Build page broke). `tests/test_bugfix_20261119_01_build_editor.py::test_no_template_puts_tojson_outside_a_script_element` fails the build if any template does this. The established examples are `avenger-layout` (`avengerLayoutDraft`), the research graph, and the build editor (`buildEditor`).

Follow-up (19/11/2026): the soldier form's `x-data` was converted to the named `soldierForm` component (`static/js/soldier_form.js`) fed by an integer parsed server-side into `data-class-id`. `tests/test_bugfix_20261119_02_related_issues.py::test_no_template_interpolates_request_derived_values_into_x_data` forbids request-derived values in any `x-data` and lists the two remaining integer-only interpolations (`avenger/_cell_modal.html`, `campaigns/sources.html`) so that debt stays visible.
