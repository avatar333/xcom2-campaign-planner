# Vendored front-end libraries

These files are downloaded once at build/scaffold time and committed so the
application has zero runtime network dependency (NFR-4, ADR-0006, ADR-0015).
They are never fetched from a CDN by the running app.

| File | Library | Version | License |
|---|---|---|---|
| `htmx.min.js` | [htmx](https://htmx.org/) | 1.9.12 | BSD 2-Clause |
| `alpinejs.min.js` | [Alpine.js](https://alpinejs.dev/) | 3.14.1 (`cdn.min.js` build) | MIT |
| `cytoscape.min.js` | [Cytoscape.js](https://js.cytoscape.org/) | 3.35.0 (`dist/cytoscape.min.js`) | MIT |
| `dagre.min.js` | [dagre](https://github.com/dagrejs/dagre) (`@dagrejs/dagre`) | 1.1.8 (`dist/dagre.min.js`) | MIT |
| `cytoscape-dagre.js` | [cytoscape-dagre](https://github.com/cytoscape/cytoscape.js-dagre) | 2.5.0 | MIT |

The last three are loaded **only** on the dependency-graph page (`research/path.html`,
Phase 6, `ADR-0021`): Cytoscape renders/interacts with the graph, dagre computes the
layered left-to-right layout. Obtained with `npm pack` from the official package
registry and copied verbatim.

To upgrade: download the new version's minified build from the project's
official release and overwrite the file in place, updating the version in
this table.
