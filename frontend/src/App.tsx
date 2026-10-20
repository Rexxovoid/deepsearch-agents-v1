import {
  ApiOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  ToolOutlined
} from "@ant-design/icons";
import { Alert, App as AntApp, Button } from "antd";
import { useEffect, useState } from "react";
import { ChatComposer } from "./components/ChatComposer";
import { ConversationThread } from "./components/ConversationThread";
import type { ChatTurn } from "./components/ConversationThread";
import { API_BASE_URL, WS_BASE_URL } from "./lib/config";
import { listConversations, type Conversation } from "./lib/api";
import { useDeepAgentSession } from "./hooks/useDeepAgentSession";
import { useFollowLatest } from "./hooks/useFollowLatest";
import type { ConnectionState, UploadedItem } from "./types";

function connectionLabel(state: ConnectionState): string {
  const labels: Record<ConnectionState, string> = {
    connecting: "连接中",
    connected: "已连接",
    reconnecting: "重连中",
    closed: "已关闭"
  };
  return labels[state];
}

function createTurn(content: string): ChatTurn {
  return {
    id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`,
    content,
    events: [],
    files: [],
    isRunning: true,
    result: "",
    timestamp: new Date().toISOString()
  };
}

export default function App({ username, onLogout }: { username: string; onLogout: () => void }) {
  const { message } = AntApp.useApp();
  const [query, setQuery] = useState("");
  const [stagedItems, setStagedItems] = useState<UploadedItem[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const session = useDeepAgentSession();
  const stream = useFollowLatest<HTMLElement>(turns, session.threadId);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  useEffect(() => {
    let active = true;
    const refresh = () => listConversations().then(items => { if (active) setConversations(items); }).catch(() => {});
    void refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [session.threadId, session.isRunning]);

  function selectConversation(id: string) {
    if (id === session.threadId) return;
    setTurns([]);
    setQuery("");
    setStagedItems([]);
    session.selectSession(id);
  }

  useEffect(() => {
    if (session.restoredTurns !== null) {
      setTurns(session.restoredTurns);
    }
  }, [session.restoredTurns]);

  useEffect(() => {
    setTurns((previous) => {
      const queued = session.events.find(event => event.event === "task_queued");
      const jobId = typeof queued?.data.job_id === "string" ? queued.data.job_id : undefined;
      let current = previous;
      const latest = previous[previous.length - 1];
      if (jobId && latest?.jobId !== jobId) {
        if (latest && !latest.jobId && latest.isRunning && latest.content === queued?.data.query) {
          current = [...previous.slice(0, -1), { ...latest, jobId }];
        } else {
          current = [...previous, { ...createTurn(String(queued?.data.query ?? "重新提交的研搜任务")), jobId }];
        }
      }
      if (current.length === 0) {
        return previous;
      }

      const latestTurn = current[current.length - 1];
      const nextLatestTurn = {
        ...latestTurn,
        events: session.events,
        files: session.files,
        isRunning: session.isRunning,
        result: session.result
      };

      return [...current.slice(0, -1), nextLatestTurn];
    });
  }, [session.events, session.files, session.isRunning, session.result]);

  async function handleSubmit() {
    if (session.connectionState !== "connected") { message.info("正在恢复会话，请稍后再提交"); return; }
    if (session.isRunning || session.isUploading) return;
    const cleanQuery = query.trim();
    if (!cleanQuery) {
      message.warning("请输入研搜任务");
      return;
    }

    const nextTurn = createTurn(cleanQuery);
    stream.resumeFollowing();
    setTurns((previous) => [...previous, nextTurn]);
    setQuery("");

    try {
      await session.submitTask(cleanQuery);
      message.success("任务已加入队列，执行过程会显示在对话中");
    } catch (error) {
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === nextTurn.id
            ? {
                ...turn,
                isRunning: false,
                result: error instanceof Error ? error.message : "任务启动失败"
              }
            : turn
        )
      );
      message.error(error instanceof Error ? error.message : "任务启动失败");
    }
  }

  async function handleCancel() {
    try {
      const response = await session.cancelCurrentTask();
      message.info(response.status === "cancelling" ? "取消请求已发送，正在等待当前调用结束" : "任务已取消");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "取消任务失败");
    }
  }

  async function handleUpload(items: UploadedItem[]) {
    try {
      const response = await session.uploadFiles(items);
      setStagedItems([]);
      message.success(`已上传 ${response.files.length} 个文件`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "上传失败");
    }
  }

  function handleNewSession() {
    session.resetSession();
    setTurns([]);
    setQuery("");
    setStagedItems([]);
  }

  const online = session.connectionState === "connected";
  const taskLabel = session.taskStatus === "queued" ? `排队中${session.queuePosition ? ` · 第 ${session.queuePosition} 位` : ""}`
    : session.taskStatus === "retry_wait" ? "等待重试"
    : session.taskStatus === "cancelling" ? "正在取消"
    : session.isRunning ? "研搜中" : "待命";

  async function handleRetry() {
    try {
      await session.retryFailedTask();
      message.success("失败任务已重新加入队列");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "重新提交失败");
    }
  }

  return (
    <div className="chat-app-shell min-h-dvh">
      <aside className="chat-sidebar" aria-label="会话信息">
        <div className="sidebar-brand">
          <span className="panel-kicker">DEEPSEARCH</span>
          <h1>深度研搜</h1>
          <p>对话式多智能体研究台</p>
        </div>

        <div className="account-row"><span>{username}</span><Button size="small" onClick={onLogout}>退出登录</Button></div>

        <Button className="new-chat-button" block onClick={handleNewSession}>
          新建研搜
        </Button>

        <nav className="conversation-list" aria-label="历史会话">
          <span className="sidebar-label">历史研搜</span>
          {conversations.length === 0 && <p className="history-empty">提交第一个问题后，会话将保存在这里</p>}
          {conversations.map(item => (
            <button key={item.thread_id} className={`conversation-item ${item.thread_id === session.threadId ? "is-active" : ""}`}
              title={item.title} aria-current={item.thread_id === session.threadId ? "page" : undefined}
              onClick={() => selectConversation(item.thread_id)}>{item.title}</button>
          ))}
        </nav>

        <div className="sidebar-status-list">
          <div className={`sidebar-status ${online ? "sidebar-status--online" : "sidebar-status--warn"}`}>
            <ApiOutlined aria-hidden />
            <span>WebSocket</span>
            <strong>{connectionLabel(session.connectionState)}</strong>
          </div>
          <div className="sidebar-status">
            <BranchesOutlined aria-hidden />
            <span>助手调度</span>
            <strong>{session.stats.assistantEvents}</strong>
          </div>
          <div className="sidebar-status">
            <ToolOutlined aria-hidden />
            <span>工具调用</span>
            <strong>{session.stats.toolEvents}</strong>
          </div>
          <div className={session.stats.errorEvents > 0 ? "sidebar-status sidebar-status--error" : "sidebar-status"}>
            <CloseCircleOutlined aria-hidden />
            <span>异常</span>
            <strong>{session.stats.errorEvents}</strong>
          </div>
        </div>

        <div className="sidebar-section">
          <span className="sidebar-label">AGENTS</span>
          <ul className="agent-mini-list">
            <li>
              <CloudServerOutlined aria-hidden />
              网络搜索助手
            </li>
            <li>
              <DatabaseOutlined aria-hidden />
              数据库查询助手
            </li>
            <li>
              <FileSearchOutlined aria-hidden />
              RAGFlow 助手
            </li>
          </ul>
        </div>

        <div className="sidebar-section sidebar-endpoints">
          <span className="sidebar-label">ENDPOINTS</span>
          <code>{API_BASE_URL}</code>
          <code>{WS_BASE_URL}</code>
        </div>
      </aside>

      <main className="chat-main">
        <header className="chat-topbar">
          <div>
            <span className="panel-kicker">CHAT WORKSPACE</span>
            <h2>深度研搜对话</h2>
          </div>
          <div className="chat-topbar-actions">
            {session.taskStatus === "failed" && <Button onClick={handleRetry}>重试任务</Button>}
            {!stream.isFollowing && <Button onClick={stream.resumeFollowing}>回到最新</Button>}
            <div className={`run-indicator ${session.isRunning ? "run-indicator--live" : ""}`}>
              {session.isRunning ? <BranchesOutlined aria-hidden /> : <CheckCircleOutlined aria-hidden />}
              {taskLabel}
            </div>
          </div>
        </header>

        {session.lastError ? (
          <Alert
            className="chat-alert"
            message={session.lastError}
            showIcon
            type="error"
          />
        ) : null}

        <section className="chat-stream-panel" ref={stream.ref} onScroll={stream.onScroll} onWheel={stream.onWheel}>
          <ConversationThread
            onUseExample={setQuery}
            turns={turns}
          />
        </section>

        <ChatComposer
          isCancelling={session.isCancelling}
          isRunning={session.isRunning}
          isUploading={session.isUploading}
          onCancel={handleCancel}
          onNewSession={handleNewSession}
          onQueryChange={setQuery}
          onStagedItemsChange={setStagedItems}
          onSubmit={handleSubmit}
          onUpload={handleUpload}
          query={query}
          stagedItems={stagedItems}
          uploadedItems={session.uploadedItems}
        />
      </main>
    </div>
  );
}
