// Research queue ordering: drag-and-drop with a keyboard/button equivalent.
// The reorder arithmetic is a pure function so it can be unit-tested without a
// browser (tests/js/research_queue.test.js). Persistence is always the server's
// job: every change POSTs the full order, and the page reloads with the
// server-computed warnings (docs/UI_UX_SPEC.md section 3.11).
(function (root) {
  "use strict";

  // Move `movedId` so it sits where `targetId` is (before it when dragging up,
  // after it when dragging down) -- the usual sortable-list behaviour.
  function reorder(ids, movedId, targetId) {
    const from = ids.indexOf(movedId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0 || from === to) return ids.slice();
    const next = ids.slice();
    next.splice(from, 1);
    next.splice(to, 0, movedId);
    return next;
  }

  // Keyboard: Alt+ArrowUp / Alt+ArrowDown move one place; clamps at the ends.
  function shift(ids, movedId, delta) {
    const from = ids.indexOf(movedId);
    if (from < 0) return ids.slice();
    const to = Math.max(0, Math.min(ids.length - 1, from + delta));
    return reorder(ids, movedId, ids[to]);
  }

  const api = { reorder: reorder, shift: shift };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.ResearchQueueLogic = api;

  document.addEventListener("alpine:init", function () {
    Alpine.data("researchQueue", function () {
      return {
        announcement: "",
        draggingId: null,
        list() {
          return this.$root.querySelector("[data-queue-list]");
        },
        ids() {
          return Array.from(this.list().querySelectorAll("[data-object-id]")).map((el) => Number(el.dataset.objectId));
        },
        dragStart(event) {
          const item = event.target.closest("[data-object-id]");
          if (!item) return;
          this.draggingId = Number(item.dataset.objectId);
          item.classList.add("is-dragging");
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", String(this.draggingId));
        },
        dragOver(event) {
          const over = event.target.closest("[data-object-id]");
          this.list().querySelectorAll(".is-drop-target").forEach((el) => el.classList.remove("is-drop-target"));
          if (over && Number(over.dataset.objectId) !== this.draggingId) over.classList.add("is-drop-target");
        },
        drop(event) {
          const over = event.target.closest("[data-object-id]");
          if (!over || this.draggingId === null) return;
          const next = reorder(this.ids(), this.draggingId, Number(over.dataset.objectId));
          this.commit(next, "Moved to position " + (next.indexOf(this.draggingId) + 1));
        },
        dragEnd() {
          this.draggingId = null;
          this.list().querySelectorAll(".is-dragging,.is-drop-target").forEach((el) => {
            el.classList.remove("is-dragging");
            el.classList.remove("is-drop-target");
          });
        },
        keyMove(event) {
          if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
          const item = event.target.closest("[data-object-id]");
          if (!item) return;
          event.preventDefault();
          const id = Number(item.dataset.objectId);
          const next = shift(this.ids(), id, event.key === "ArrowUp" ? -1 : 1);
          this.commit(next, "Moved to position " + (next.indexOf(id) + 1), id);
        },
        commit(order, message, focusId) {
          const current = this.ids();
          if (order.join(",") === current.join(",")) return;
          const body = new URLSearchParams();
          body.append("csrf_token", this.$root.dataset.csrf);
          order.forEach((id) => body.append("order", String(id)));
          this.announcement = message;
          fetch(this.$root.dataset.reorderUrl, {
            method: "POST",
            headers: { "X-Requested-With": "fetch", Accept: "application/json" },
            body: body,
            credentials: "same-origin",
          })
            .then((response) => response.json().catch(() => ({ ok: response.ok })))
            .then((result) => {
              if (focusId) sessionStorage.setItem("rs-focus", String(focusId));
              if (!result.ok) this.announcement = result.error || "Could not save the new order.";
              window.location.reload();
            })
            .catch(() => {
              this.announcement = "Could not save the new order.";
            });
        },
        init() {
          const focusId = sessionStorage.getItem("rs-focus");
          if (focusId) {
            sessionStorage.removeItem("rs-focus");
            const el = this.$root.querySelector('[data-object-id="' + focusId + '"]');
            if (el) el.focus();
          }
        },
      };
    });
  });
})(typeof window !== "undefined" ? window : globalThis);
