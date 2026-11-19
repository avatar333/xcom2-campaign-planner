// Logic of the buildEditor Alpine component (app/static/js/build_editor.js), run without a browser.
const fs = require("fs");
const path = require("path");
const assert = require("assert");

let stateJson = "{}";
let factory = null;
const listeners = {};
global.document = {
  addEventListener: (name, fn) => { listeners[name] = fn; },
  getElementById: (id) => (id === "bw-editor-state" ? { textContent: stateJson } : null),
};
global.Alpine = { data: (name, fn) => { if (name === "buildEditor") factory = fn; } };
eval(fs.readFileSync(path.join(__dirname, "../../app/static/js/build_editor.js"), "utf8"));
listeners["alpine:init"]();
assert(factory, "component must register on alpine:init");

// An element standing in for a <label class="bw-card"> with its data-* identity.
const card = (slotType, index, objectId) => ({ dataset: { slotType, slotIndex: String(index), objectId: objectId === null ? "" : String(objectId) } });

// Awkward slot types and ids must be handled as plain data.
const nasty = `x'y"z</script><b>`;
stateJson = JSON.stringify({ step: 3, baseId: 45, attachments: [
  { slotType: "weapon_upgrade", index: 0, objectId: 103 },
  { slotType: "weapon_upgrade", index: 1, objectId: 109 },
  { slotType: nasty, index: 0, objectId: 7 } ] });
const c = factory();

assert.strictEqual(c.step, 3);
assert.strictEqual(c.baseId, 45);
assert.strictEqual(c.attachments.length, 3);
assert.strictEqual(c.chosenFor("weapon_upgrade", 0), 103);
assert.strictEqual(c.chosenFor("weapon_upgrade", 1), 109);
assert.strictEqual(c.chosenFor(nasty, 0), 7);
assert.strictEqual(c.chosenFor("weapon_upgrade", 2), null);

// isSelected: the saved choice is selected, siblings and "None" are not.
assert.strictEqual(c.isSelected(card("weapon_upgrade", 0, 103)), true);
assert.strictEqual(c.isSelected(card("weapon_upgrade", 0, 104)), false);
assert.strictEqual(c.isSelected(card("weapon_upgrade", 0, null)), false);
assert.strictEqual(c.isSelected(card("weapon_upgrade", 2, null)), true, "an empty slot shows None selected");
assert.strictEqual(c.isSelected(card(nasty, 0, 7)), true);

// pick: replaces only that slot; None removes it; never duplicates.
c.pick(card("weapon_upgrade", 0, 104));
assert.strictEqual(c.chosenFor("weapon_upgrade", 0), 104);
assert.strictEqual(c.chosenFor("weapon_upgrade", 1), 109, "other slots untouched");
assert.strictEqual(c.attachments.filter((a) => a.slotType === "weapon_upgrade" && a.index === 0).length, 1);
c.pick(card("weapon_upgrade", 1, null));
assert.strictEqual(c.chosenFor("weapon_upgrade", 1), null);
assert.strictEqual(c.attachments.length, 2);
c.pick(card("weapon_upgrade", 1, 110));
c.pick(card("weapon_upgrade", 1, 110));
assert.strictEqual(c.attachments.filter((a) => a.slotType === "weapon_upgrade" && a.index === 1).length, 1);

// Create state: empty attachments, step 1, no base.
stateJson = JSON.stringify({ step: 1, baseId: null, attachments: [] });
const fresh = factory();
assert.deepStrictEqual([fresh.step, fresh.baseId, fresh.attachments.length], [1, null, 0]);

// Missing/garbled state block degrades to the Create defaults instead of throwing.
global.document.getElementById = () => null;
const blank = factory();
assert.deepStrictEqual([blank.step, blank.baseId, blank.attachments.length], [1, null, 0]);
console.log("build editor component: OK");
