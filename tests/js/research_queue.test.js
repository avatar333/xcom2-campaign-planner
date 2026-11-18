// Pure ordering logic used by the research queue's drag-and-drop and Alt+Arrow keys.
const assert = require("assert");
global.document = { addEventListener: () => {} };
const { reorder, shift } = require("../../app/static/js/research_queue.js");

const ids = [10, 20, 30, 40];

// Dragging down: the moved item lands where the target was.
assert.deepStrictEqual(reorder(ids, 10, 30), [20, 30, 10, 40]);
// Dragging up.
assert.deepStrictEqual(reorder(ids, 40, 20), [10, 40, 20, 30]);
// Dropping on itself, or on/with unknown ids, changes nothing and never mutates the input.
assert.deepStrictEqual(reorder(ids, 20, 20), ids);
assert.deepStrictEqual(reorder(ids, 99, 20), ids);
assert.deepStrictEqual(reorder(ids, 20, 99), ids);
assert.deepStrictEqual(ids, [10, 20, 30, 40]);

// Keyboard moves one place and clamps at both ends.
assert.deepStrictEqual(shift(ids, 30, -1), [10, 30, 20, 40]);
assert.deepStrictEqual(shift(ids, 30, 1), [10, 20, 40, 30]);
assert.deepStrictEqual(shift(ids, 10, -1), ids);
assert.deepStrictEqual(shift(ids, 40, 1), ids);
assert.deepStrictEqual(shift(ids, 99, 1), ids);

// Every result is a permutation of the input (the server rejects anything else).
for (const moved of ids) for (const target of ids) {
  assert.deepStrictEqual(reorder(ids, moved, target).slice().sort(), ids.slice().sort());
}
console.log("research queue ordering: OK");
