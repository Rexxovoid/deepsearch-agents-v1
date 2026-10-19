function storageKey() { return `deepsearch.thread_id.${window.localStorage.getItem("deepsearch.account") ?? ""}`; }

export function createThreadId(): string {
  if (crypto.randomUUID) {
    return crypto.randomUUID();
  }

  return `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function getStoredThreadId(): string {
  const existing = window.localStorage.getItem(storageKey());
  if (existing) {
    return existing;
  }

  const threadId = createThreadId();
  window.localStorage.setItem(storageKey(), threadId);
  return threadId;
}

export function storeThreadId(threadId: string): void {
  window.localStorage.setItem(storageKey(), threadId);
}
