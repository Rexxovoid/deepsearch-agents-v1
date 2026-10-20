// Run: node tests/test_scroll_follow.cjs
// Exercise the real hook with a minimal hook/animation scheduler and scroll pane.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("../frontend/node_modules/typescript");
const slots = [];
let index = 0;
let effects = [];
let nextFrame = 0;
const frames = new Map();
const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
const react = {
  useRef(value) { const i = index++; return slots[i] ??= { current: value }; },
  useState(value) {
    const i = index++;
    slots[i] ??= { value };
    return [slots[i].value, (next) => { slots[i].value = next; }];
  },
  useCallback(fn, deps) {
    const i = index++;
    if (!same(slots[i]?.deps, deps)) slots[i] = { value: fn, deps };
    return slots[i].value;
  },
  useEffect(fn, deps) {
    const i = index++;
    if (!same(slots[i]?.deps, deps)) effects.push(() => {
      slots[i]?.cleanup?.();
      slots[i] = { deps, cleanup: fn() };
    });
  }
};
const animation = {
  requestAnimationFrame(fn) { frames.set(++nextFrame, fn); return nextFrame; },
  cancelAnimationFrame(id) { frames.delete(id); }
};
const source = fs.readFileSync(path.join(__dirname, "../frontend/src/hooks/useFollowLatest.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const exportsObject = {};
new Function("require", "exports", "window", compiled)((id) => {
  assert.equal(id, "react"); return react;
}, exportsObject, animation);
const pane = {
  scrollHeight: 1000, clientHeight: 100, top: 0,
  get scrollTop() { return this.top; },
  set scrollTop(value) { this.top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); }
};
function render(content, session = "a") {
  index = 0; effects = [];
  const hook = exportsObject.useFollowLatest(content, session);
  hook.ref.current = pane;
  effects.forEach(fn => fn());
  return hook;
}
function flush() {
  const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(fn => fn());
}

let hook = render(1); flush();
assert.equal(pane.scrollTop, 900, "initial history opens at the latest message");

pane.scrollTop = 300; hook.onScroll({ currentTarget: pane });
pane.scrollHeight = 1200; hook = render(2); flush();
assert.equal(pane.scrollTop, 300, "new content preserves the historical reading position");
assert.equal(hook.isFollowing, false);
hook = render({ files: "poll update" }); flush();
assert.equal(pane.scrollTop, 300, "periodic polling must not pull the reader down");

pane.scrollTop = 1100; hook.onScroll({ currentTarget: pane });
pane.scrollHeight = 1400; hook = render(3); flush();
assert.equal(pane.scrollTop, 1300, "returning to the bottom resumes following");

pane.scrollHeight = 1600; hook = render(4);
hook.onWheel({ deltaY: -120 }); pane.scrollTop = 1250;
flush();
assert.equal(pane.scrollTop, 1250, "scrolling up cancels an already scheduled automatic jump");

hook.resumeFollowing();
assert.equal(pane.scrollTop, 1500, "explicit return-to-latest jumps to the bottom");

pane.scrollTop = 200; hook.onScroll({ currentTarget: pane });
hook = render(5, "b"); flush();
assert.equal(pane.scrollTop, 1500, "switching sessions starts with the latest message");

render(6);
slots.forEach(slot => slot?.cleanup?.());
assert.equal(frames.size, 0, "cleanup cancels pending animation frames");
console.log("PASS: initial follow, history pause, polling, resume, wheel/frame race, session reset and cleanup");
