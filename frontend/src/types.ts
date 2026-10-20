export type ConnectionState = "connecting" | "connected" | "reconnecting" | "closed";

export type MonitorEventName =
  | "session_created"
  | "tool_start"
  | "assistant_call"
  | "task_result"
  | "task_cancelled"
  | "error"
  | string;

export interface MonitorMessage {
  id?: number;
  type: "monitor_event";
  event: MonitorEventName;
  message: string;
  data: Record<string, unknown>;
  timestamp: string;
}

export interface PongMessage {
  type: "pong";
  message: string;
}

export interface EventHistoryMessage {
  type: "event_history";
  events: MonitorMessage[];
  is_running: boolean;
  task?: QueueTask | null;
}

export type QueueTaskStatus = "idle" | "queued" | "running" | "retry_wait" | "cancelling" | "succeeded" | "failed" | "cancelled";
export interface QueueTask {
  job_id?: string;
  status: QueueTaskStatus;
  is_running: boolean;
  queue_position: number | null;
  attempt?: number;
  max_retries?: number;
  last_error?: string;
}

export type SocketMessage = MonitorMessage | PongMessage | EventHistoryMessage;

export interface TaskResponse {
  status: "started" | string;
  thread_id: string;
}

export interface CancelTaskResponse {
  status: "cancelled" | "cancelling" | string;
  thread_id: string;
  message?: string;
}

export interface UploadResponse {
  status: "uploaded" | string;
  files: string[];
}

export interface OutputFile {
  name: string;
  type: "file" | string;
  path: string;
  size: number;
  mtime: number;
}

export interface FileListResponse {
  files?: OutputFile[];
  error?: string;
}

export interface UploadedItem {
  uid: string;
  name: string;
  size: number;
  raw: File;
}
