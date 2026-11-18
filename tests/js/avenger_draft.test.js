// Exercises app/static/js/avenger.js (configure-mode draft editor) without a browser.
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const coordinates = [];
for (let r = 1; r <= 4; r++) for (let c = 1; c <= 3; c++) coordinates.push(`R${r}C${c}`);
const rooms = {
  "1": { id: 1, name: "Empty / Cleared", blocks: false, glyph: "<svg/>", image: null },
  "2": { id: 2, name: "Alien Debris", blocks: true, glyph: "<svg/>", image: null },
};
const data = {
  coordinates,
  initial: Object.fromEntries(coordinates.map((c) => [c, null])),
  rooms,
  order: [2, 1],
  defaultEmptyId: 1,
  columns: 3,
};
data.initial.R1C1 = 2;

let factory = null;
const listeners = {};
global.document = {
  addEventListener: (name, fn) => { listeners[name] = fn; },
  getElementById: () => ({ textContent: JSON.stringify(data) }),
};
global.window = { confirm: () => false };
global.Alpine = { data: (name, fn) => { if (name === "avengerLayoutDraft") factory = fn; } };

eval(fs.readFileSync(path.join(__dirname, "../../app/static/js/avenger.js"), "utf8"));
listeners["alpine:init"]();
assert(factory, "component must register on alpine:init");

const make = () => factory();
let draft = make();

// Starts on the first unconfigured cell; nothing is dirty yet.
assert.strictEqual(draft.selected, "R1C2");
assert.strictEqual(draft.configuredCount, 1);
assert.strictEqual(draft.dirty, false);
assert.deepStrictEqual(draft.roomList.map((r) => r.name), ["Alien Debris", "Empty / Cleared"]);

// Rapid clicking: choosing a room sets the cell and advances to the next one.
draft.choose(1);
assert.strictEqual(draft.draft.R1C2, 1);
assert.strictEqual(draft.selected, "R1C3");
draft.choose(2);
draft.choose(1);
assert.strictEqual(draft.draft.R1C3, 2);
assert.strictEqual(draft.draft.R2C1, 1);
assert.strictEqual(draft.selected, "R2C2");
assert.strictEqual(draft.dirty, true);

// Walking through all remaining cells stops at the last one rather than wrapping.
draft.select("R4C3");
draft.choose(1);
assert.strictEqual(draft.selected, "R4C3");
assert.strictEqual(draft.draft.R4C3, 1);

// Reset to EMPTY fills every cell of the draft only.
draft.resetToEmpty();
assert.strictEqual(draft.configuredCount, 12);
assert(coordinates.every((c) => draft.draft[c] === 1));
assert.strictEqual(draft.saved.R1C1, 2, "saved baseline is untouched until the server saves");

// Cancel with unsaved changes asks first; declining keeps the user on the page.
let prevented = false;
draft.confirmCancel({ preventDefault: () => { prevented = true; } });
assert.strictEqual(prevented, true);
window.confirm = () => true;
prevented = false;
draft.confirmCancel({ preventDefault: () => { prevented = true; } });
assert.strictEqual(prevented, false);

// A pristine draft cancels without prompting.
draft = make();
window.confirm = () => { throw new Error("should not prompt"); };
draft.confirmCancel({ preventDefault: () => { throw new Error("should not prevent"); } });

// room(): resolves ids to catalog room data, null when unset.
assert.strictEqual(draft.room("R1C1").name, "Alien Debris");
assert.strictEqual(draft.room("R4C1"), null);
console.log("avenger draft editor: OK");
