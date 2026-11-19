// Build editor (Weapon / Armour builds, Create and Edit share this component).
// Component logic lives here; the server supplies only DATA, as JSON in a
// <script type="application/json" id="bw-editor-state"> block (serialised with
// Jinja's `tojson`, which is safe inside a script element). Nothing from the
// server is ever written into JavaScript source or into an x-data attribute.
//
// State shape (identical for Create and Edit, see app/routes/soldiers.py
// `_editor_state`):
//   { step: 1..4, baseId: number|null,
//     attachments: [ { slotType: string, index: number, objectId: number } ] }
document.addEventListener("alpine:init", () => {
  Alpine.data("buildEditor", () => {
    const stateEl = document.getElementById("bw-editor-state");
    const initial = stateEl ? JSON.parse(stateEl.textContent) : {};
    return {
      step: initial.step || 1,
      baseId: initial.baseId === undefined ? null : initial.baseId,
      attachments: (initial.attachments || []).map((a) => ({
        slotType: String(a.slotType),
        index: Number(a.index),
        objectId: Number(a.objectId),
      })),

      // A choice element (<label class="bw-card">) carries its identity in
      // data-slot-type / data-slot-index / data-object-id ("" = the None card).
      keyOf(el) {
        return { slotType: el.dataset.slotType, index: Number(el.dataset.slotIndex) };
      },
      chosenFor(slotType, index) {
        const hit = this.attachments.find((a) => a.slotType === slotType && a.index === index);
        return hit ? hit.objectId : null;
      },
      isSelected(el) {
        const { slotType, index } = this.keyOf(el);
        const wanted = el.dataset.objectId === "" ? null : Number(el.dataset.objectId);
        return this.chosenFor(slotType, index) === wanted;
      },
      pick(el) {
        const { slotType, index } = this.keyOf(el);
        this.attachments = this.attachments.filter((a) => !(a.slotType === slotType && a.index === index));
        if (el.dataset.objectId !== "") {
          this.attachments.push({ slotType, index, objectId: Number(el.dataset.objectId) });
        }
      },
    };
  });
});
