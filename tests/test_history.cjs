// Run: node tests/test_history.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("../frontend/node_modules/typescript");
const filename = path.join(__dirname, "../frontend/src/lib/history.ts");
const compiled = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 }
}).outputText;
const resultModule = { exports: {} };
new Function("exports", "module", compiled)(resultModule.exports, resultModule);
const { restoreHistory } = resultModule.exports;

const event = (id, name, data = {}, message = name) => ({
  id, type: "monitor_event", event: name, data, message, timestamp: `2026-10-13T10:00:${id}`
});
const history = [
  event(1, "task_started", { query: "第一轮问题" }),
  event(2, "session_created", { path: "session/path" }),
  event(3, "task_result", { result: "第一轮答案" }),
  event(4, "task_started", { query: "第二轮问题" }),
  event(5, "tool_start", { tool_name: "search" })
];
const running = restoreHistory(history, true);
assert.equal(running.turns.length, 2);
assert.equal(running.turns[0].result, "第一轮答案");
assert.equal(running.turns[0].isRunning, false);
assert.equal(running.turns[1].content, "第二轮问题");
assert.equal(running.turns[1].isRunning, true);
assert.equal(running.result, "");
assert.equal(running.sessionPath, "session/path");
assert.deepEqual(running.events.map(e => e.id), [4, 5]);

const finished = restoreHistory([...history, event(6, "task_result", { result: "第二轮答案" })], false);
assert.equal(finished.result, "第二轮答案");
assert.equal(finished.turns[1].isRunning, false);

const interrupted = restoreHistory([...history, event(6, "error", {}, "任务已中断")], false);
assert.equal(interrupted.lastError, "任务已中断");
assert.equal(interrupted.result, "任务已中断");
assert.equal(interrupted.turns[1].isRunning, false);

const cancelled = restoreHistory([...history, event(6, "task_cancelled", {}, "任务已取消")], false);
assert.equal(cancelled.result, "任务已取消");
assert.deepEqual(restoreHistory([], false).turns, []);

const queued = [event(1, "task_queued", { query: "queued question", job_id: "job-1" })];
assert.equal(restoreHistory(queued, true).turns[0].jobId, "job-1");
const retried = [...queued,
  event(2, "task_running", { job_id: "job-1", attempt: 1 }),
  event(3, "task_result", { job_id: "job-1", result: "partial" }),
  event(4, "task_retrying", { job_id: "job-1" }),
  event(5, "task_running", { job_id: "job-1", attempt: 2 })];
assert.equal(restoreHistory(retried, true).turns.length, 1, "an automatic retry is the same turn");
assert.equal(restoreHistory(retried, true).result, "", "failed-attempt output is cleared before retry");
const finalFailure = restoreHistory([...retried,
  event(6, "task_result", { job_id: "job-1", result: "not-final" }),
  event(7, "error", { job_id: "job-1" }, "failed permanently")], false);
assert.equal(finalFailure.result, "failed permanently");
const manualRetry = restoreHistory([...retried,
  event(6, "task_queued", { job_id: "job-2", query: "queued question" })], true);
assert.equal(manualRetry.turns.length, 2, "manual resubmission creates its own turn");
console.log("PASS: multiple turns, running refresh, result, interruption, cancellation, empty session");
