# Vendored front-end libraries

These files are downloaded once at build/scaffold time and committed so the
application has zero runtime network dependency (NFR-4, ADR-0006, ADR-0015).
They are never fetched from a CDN by the running app.

| File | Library | Version | License |
|---|---|---|---|
| `htmx.min.js` | [htmx](https://htmx.org/) | 1.9.12 | BSD 2-Clause |
| `alpinejs.min.js` | [Alpine.js](https://alpinejs.dev/) | 3.14.1 (`cdn.min.js` build) | MIT |

To upgrade: download the new version's minified build from the project's
official release and overwrite the file in place, updating the version in
this table.
