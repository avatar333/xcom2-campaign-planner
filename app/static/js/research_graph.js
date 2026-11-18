// Interactive unlock-path graph (Cytoscape.js + dagre layered layout).
// Rendering only: the graph itself -- every ancestor, AND/OR gates, statuses --
// is computed server-side (app/domain/dependency_graph.py) and embedded as JSON.
(function () {
  "use strict";
  const dataEl = document.getElementById("rs-graph-data");
  const container = document.getElementById("rs-graph");
  if (!dataEl || !container || typeof cytoscape === "undefined") return;

  const data = JSON.parse(dataEl.textContent);
  if (typeof cytoscapeDagre !== "undefined") cytoscape.use(cytoscapeDagre);

  const COLOURS = {
    completed: "#59c97a", available: "#35d1e6", planned: "#8fb7ff",
    in_progress: "#e8b64c", blocked: "#ef5b5b", gate: "#b58cff",
  };

  const cy = cytoscape({
    container: container,
    elements: { nodes: data.nodes, edges: data.edges },
    minZoom: 0.2,
    maxZoom: 2.2,
    wheelSensitivity: 0.25,
    style: [
      {
        selector: "node[kind = 'object']",
        style: {
          shape: "round-rectangle", width: 210, height: 76,
          "background-color": "#101a26", "background-image": "data(image)",
          "background-fit": "none", "background-width": 42, "background-height": 42,
          "background-position-x": 10, "background-position-y": "50%",
          "background-clip": "node", "background-image-containment": "inside",
          label: "data(label)", color: "#eaf2f8", "font-size": 11, "line-height": 1.25,
          "text-wrap": "wrap", "text-max-width": 136, "text-valign": "center", "text-halign": "center",
          "text-margin-x": 28, "border-width": 2, "border-color": "#2f4a63", "border-style": "solid",
        },
      },
      { selector: "node.status-completed", style: { "border-color": COLOURS.completed, "border-width": 3, "border-style": "solid", "background-color": "#12261b" } },
      { selector: "node.status-available", style: { "border-color": COLOURS.available, "border-width": 3, "border-style": "solid" } },
      { selector: "node.status-planned", style: { "border-color": COLOURS.planned, "border-width": 3, "border-style": "dashed" } },
      { selector: "node.status-in_progress", style: { "border-color": COLOURS.in_progress, "border-width": 6, "border-style": "double" } },
      { selector: "node.status-blocked", style: { "border-color": COLOURS.blocked, "border-width": 3, "border-style": "dotted", "background-color": "#1a1215" } },
      {
        selector: "node.is-target",
        style: { "border-width": 6, "underlay-color": "#5be8ff", "underlay-opacity": 0.28, "underlay-padding": 10, "font-weight": "bold" },
      },
      {
        selector: "node[kind = 'gate']",
        style: {
          shape: "diamond", width: 96, height: 62, "background-color": "#1b1530",
          "border-width": 2, "border-color": COLOURS.gate, "border-style": "dashed",
          label: "data(label)", color: "#d9c8ff", "font-size": 9, "font-weight": "bold",
          "text-wrap": "wrap", "text-max-width": 70, "text-valign": "center", "text-halign": "center",
        },
      },
      { selector: "node[kind = 'gate'][satisfied]", style: { "border-style": "solid" } },
      {
        selector: "edge",
        style: {
          width: 2, "line-color": "#4b6a85", "target-arrow-color": "#4b6a85", "target-arrow-shape": "triangle",
          "curve-style": "bezier", "arrow-scale": 1.2, label: "data(label)", "font-size": 10, color: "#9fb4c4",
          "text-background-color": "#0a1118", "text-background-opacity": 1, "text-background-padding": 2,
        },
      },
      { selector: "edge[?gating = false]", style: { "line-style": "dotted", "line-color": "#6b7b8a", "target-arrow-color": "#6b7b8a" } },
      { selector: "edge[?cycle]", style: { "line-color": COLOURS.blocked, "target-arrow-color": COLOURS.blocked, "line-style": "dashed", width: 3 } },
      { selector: ".faded", style: { opacity: 0.18 } },
      { selector: "node:selected", style: { "overlay-color": "#5be8ff", "overlay-opacity": 0.2, "overlay-padding": 6 } },
    ],
  });

  cy.nodes().forEach((n) => {
    n.addClass(n.data("kind") === "gate" ? "gate" : "status-" + n.data("status"));
    if (n.data("isTarget")) n.addClass("is-target");
  });

  let direction = null;
  function runLayout(dir, animate) {
    cy.layout({
      name: typeof cytoscapeDagre !== "undefined" ? "dagre" : "breadthfirst",
      rankDir: dir, nodeSep: 22, rankSep: 64, edgeSep: 12, ranker: "network-simplex",
      fit: true, padding: 20, animate: !!animate, animationDuration: 220,
    }).run();
  }
  // Long prerequisite chains are far wider than they are tall (or the reverse),
  // so start in whichever orientation leaves the nodes larger once fitted.
  function chooseDirection() {
    runLayout("LR");
    const wide = cy.zoom();
    runLayout("TB");
    const tall = cy.zoom();
    return tall > wide ? "TB" : "LR";
  }
  direction = chooseDirection();
  runLayout(direction);
  const dirButton = document.getElementById("rs-dir");
  function labelDirection() {
    dirButton.textContent = direction === "LR" ? "Top–down" : "Left–right";
    dirButton.setAttribute("aria-pressed", direction === "TB" ? "true" : "false");
  }
  labelDirection();

  document.getElementById("rs-fit").addEventListener("click", () => cy.fit(undefined, 24));
  dirButton.addEventListener("click", () => {
    direction = direction === "LR" ? "TB" : "LR";
    labelDirection();
    runLayout(direction, true);
  });
  document.getElementById("rs-hide-done").addEventListener("change", (event) => {
    const done = cy.nodes("[kind = 'object'][status = 'completed'][!isTarget]");
    if (event.target.checked) done.style("display", "none"); else done.style("display", "element");
    cy.edges().forEach((e) => {
      e.style("display", e.source().style("display") === "none" || e.target().style("display") === "none" ? "none" : "element");
    });
    runLayout(direction, true);
  });

  // Selecting a node highlights everything it depends on and what it feeds.
  const panel = document.getElementById("rs-node-panel");
  function describe(node) {
    panel.replaceChildren();
    if (node.data("kind") === "gate") {
      const strong = document.createElement("strong");
      strong.textContent = node.data("logic") === "OR" ? "Alternatives" : "All required";
      panel.append(strong, document.createTextNode(
        node.data("logic") === "OR" ? " \u2014 satisfy any ONE of the inputs on the left." : " \u2014 every input must be satisfied."));
      return;
    }
    const title = document.createElement("strong");
    title.textContent = node.data("name");
    const meta = document.createElement("span");
    meta.textContent = " \u00b7 " + node.data("type") + " \u00b7 " + node.data("statusLabel");
    const link = document.createElement("a");
    link.href = node.data("url");
    link.className = "btn btn--sm";
    link.textContent = "Open details";
    panel.append(title, meta, document.createTextNode(" "), link);
  }
  cy.on("tap", "node", (event) => {
    const node = event.target;
    cy.elements().addClass("faded");
    node.closedNeighborhood().removeClass("faded");
    node.predecessors().removeClass("faded");
    node.successors().removeClass("faded");
    describe(node);
  });
  cy.on("tap", (event) => {
    if (event.target === cy) {
      cy.elements().removeClass("faded");
      panel.replaceChildren(Object.assign(document.createElement("span"), { className: "text-muted", textContent: "Select a node for details and actions." }));
    }
  });
  cy.on("dbltap", "node[kind = 'object']", (event) => { window.location.href = event.target.data("url"); });

  window.__researchGraph = cy; // exposed for diagnostics and browser tests
})();
