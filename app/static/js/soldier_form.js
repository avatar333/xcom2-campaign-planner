// Soldier create/edit form. The only state is which class card is chosen, and it
// arrives as a validated integer in a data attribute (data-class-id), never as
// JavaScript source (docs/adr/ADR-0015 amendment).
document.addEventListener("alpine:init", () => {
  Alpine.data("soldierForm", () => ({
    classId: null,
    init() {
      const raw = this.$el.dataset.classId;
      this.classId = raw ? Number(raw) : null;
    },
  }));
});
