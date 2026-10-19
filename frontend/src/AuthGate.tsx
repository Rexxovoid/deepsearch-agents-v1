import { Alert, Button, Input, Spin } from "antd";
import { useEffect, useState } from "react";
import App from "./App";
import { API_BASE_URL } from "./lib/config";

export default function AuthGate() {
  const [username, setUsername] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [register, setRegister] = useState(false);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    fetch(`${API_BASE_URL}/api/auth/me`, { credentials: "include" }).then(async response => {
      if (response.ok) { const data = await response.json(); window.localStorage.setItem("deepsearch.account", data.username); setUsername(data.username); }
    }).catch(() => setError("无法连接后端服务")).finally(() => setLoading(false));
  }, []);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setLoading(true); setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/${register ? "register" : "login"}`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: name, password })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "登录失败，请检查输入");
      window.localStorage.setItem("deepsearch.account", data.username);
      setUsername(data.username); setPassword("");
    } catch (error) { setError(error instanceof Error ? error.message : "登录失败"); }
    finally { setLoading(false); }
  }
  async function logout() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/logout`, { method: "POST", credentials: "include" });
      if (!response.ok) throw new Error("退出失败，请重试");
      setUsername(null);
    } catch (error) { setError(error instanceof Error ? error.message : "退出失败"); }
  }
  if (username) return <App key={username} username={username} onLogout={logout} />;
  if (loading) return <div className="auth-screen"><Spin size="large" /></div>;
  return <div className="auth-screen"><form className="auth-card" onSubmit={submit}>
    <span className="panel-kicker">DEEPSEARCH</span><h1>深度研搜</h1>
    <p>{register ? "创建账号，保存你的研搜会话" : "登录后继续你的研搜任务"}</p>
    {error && <Alert type="error" message={error} />}
    <label>用户名<Input autoComplete="username" value={name} onChange={e => setName(e.target.value)} minLength={2} maxLength={80} required /></label>
    <label>密码<Input.Password autoComplete={register ? "new-password" : "current-password"} value={password} onChange={e => setPassword(e.target.value)} minLength={8} maxLength={256} required /></label>
    <Button type="primary" htmlType="submit" block>{register ? "注册并登录" : "登录"}</Button>
    <Button type="link" onClick={() => { setRegister(!register); setError(""); }}>{register ? "已有账号，去登录" : "创建新账号"}</Button>
  </form></div>;
}
