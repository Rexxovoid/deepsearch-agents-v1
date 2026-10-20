import type { ChatTurn } from "../components/ConversationThread";
import type { MonitorMessage } from "../types";

export function restoreHistory(events: MonitorMessage[], isRunning: boolean) {
  const turns: ChatTurn[] = [];
  let sessionPath = "";
  let lastError = "";
  for (const event of events) {
    if (event.event === "task_started" || event.event === "task_queued" || turns.length === 0) {
      turns.push({
        id: `history-${event.id ?? event.timestamp}`,
        jobId: typeof event.data.job_id === "string" ? event.data.job_id : undefined,
        content: typeof event.data.query === "string" ? event.data.query : "已恢复的研搜任务",
        events: [], files: [], isRunning: false, result: "", timestamp: event.timestamp
      });
      lastError = "";
    }
    const turn = turns[turns.length - 1];
    turn.events.push(event);
    if (event.event === "task_retrying" || event.event === "task_running") turn.result = "";
    if (event.event === "session_created" && typeof event.data.path === "string") {
      sessionPath = event.data.path;
    }
    if (event.event === "task_result") {
      turn.result = typeof event.data.result === "string" ? event.data.result : event.message;
    } else if (event.event === "task_cancelled") {
      if (event.data.job_id) turn.result = event.message;
      else turn.result ||= event.message;
    } else if (event.event === "error") {
      lastError = event.message;
      if (event.data.job_id) turn.result = event.message;
      else turn.result ||= event.message;
    }
  }
  const latest = turns[turns.length - 1];
  if (latest) latest.isRunning = isRunning;
  return {
    turns, sessionPath, lastError,
    events: latest?.events ?? [], result: latest?.result ?? ""
  };
}
