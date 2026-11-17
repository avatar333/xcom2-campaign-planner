// Thin progressive-enhancement glue (ADR-0015). No business logic here --
// every behaviour below is purely presentational / client-side convenience.
// htmx and Alpine are loaded from app/static/js/vendor (never a CDN).
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const openModal = document.querySelector("[data-modal-root]");
  if (openModal) {
    openModal.remove();
  }
});

document.body.addEventListener("click", (event) => {
  const backdrop = event.target.closest(".modal-backdrop");
  if (backdrop && event.target === backdrop) {
    backdrop.remove();
  }
});
