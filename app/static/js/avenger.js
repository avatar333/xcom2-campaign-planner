// Avenger Facility Planner: "Configure Starting Avenger" draft editor.
// Holds the whole layout draft client-side; nothing is persisted until the
// user submits the form with "Save Layout" (docs/UI_UX_SPEC.md §3.10).
// Registered before Alpine starts (script order in base.html).
document.addEventListener("alpine:init", () => {
  Alpine.data("avengerLayoutDraft", () => {
    const data = JSON.parse(document.getElementById("av-layout-data").textContent);
    const initial = {};
    data.coordinates.forEach((coordinate) => {
      initial[coordinate] = data.initial[coordinate] || null;
    });
    return {
      coordinates: data.coordinates,
      rooms: data.rooms,
      roomList: data.order.map((id) => data.rooms[String(id)]),
      defaultEmptyId: data.defaultEmptyId,
      draft: { ...initial },
      saved: { ...initial },
      selected: data.coordinates.find((c) => !initial[c]) || data.coordinates[0],
      emptyGlyph:
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">' +
        '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg>',
      room(coordinate) {
        const id = this.draft[coordinate];
        return id ? this.rooms[String(id)] || null : null;
      },
      select(coordinate) {
        this.selected = coordinate;
      },
      choose(roomId) {
        this.draft[this.selected] = roomId;
        const index = this.coordinates.indexOf(this.selected);
        if (index >= 0 && index < this.coordinates.length - 1) {
          this.selected = this.coordinates[index + 1];
        }
      },
      resetToEmpty() {
        if (!this.defaultEmptyId) return;
        this.coordinates.forEach((c) => {
          this.draft[c] = this.defaultEmptyId;
        });
      },
      get configuredCount() {
        return this.coordinates.filter((c) => this.draft[c]).length;
      },
      get dirty() {
        return this.coordinates.some((c) => this.draft[c] !== this.saved[c]);
      },
      confirmCancel(event) {
        if (this.dirty && !window.confirm("Discard unsaved layout changes?")) {
          event.preventDefault();
        }
      },
    };
  });
});
