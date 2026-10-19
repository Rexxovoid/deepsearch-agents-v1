# 2026-10-19 持久化、会话管理与权限隔离

本日志回顾本次开发确认和完善的实现，部分持久化代码在记录前已存在。

## 问题一：刷新页面后无法恢复任务界面

现象与原因：刷新会清空 React 内存状态并断开 WebSocket。仅保存会话 ID 无法恢复问题、进度与答案，断线期间的事件也需要补发。

解决方法：

1. 前端在 `localStorage` 保存会话 ID，刷新后连接原会话。
2. 后端使用独立异步任务运行 Agent，断开连接只清理连接，不取消任务。
3. SQLite 先保存有序事件再推送；重连发送 `event_history` 快照及运行状态。
4. 前端根据历史重建对话，通过事件 ID 去重，并支持断线重连。
5. 服务重启时将未完成任务标记为中断，避免页面一直显示执行中。

实现位置：`frontend/src/lib/thread.ts`、`frontend/src/lib/history.ts`、`frontend/src/hooks/useDeepAgentSession.ts`、`app/api/event_store.py`、`app/api/monitor.py`、`app/api/server.py`。

## 问题二：新建研搜后缺少历史入口，切换可能混入旧状态

现象与原因：原侧栏展示会话 ID，没有历史列表；旧 WebSocket 和异步文件请求需要在切换时处理。

解决方法：

- 后端保存会话标题和所有者，第一条问题填写标题，追问不覆盖标题。
- 前端增加历史列表、选中状态和切换操作，隐藏侧栏 ID。
- 切换时清理旧连接和显示状态，再加载所选历史；文件请求返回时检查会话是否仍匹配。
- 刷新或切换后重新加载已上传附件名称及生成文件。

修改位置：`app/api/ownership.py`、`app/api/server.py`、`frontend/src/App.tsx`、`frontend/src/styles.css`、`frontend/src/hooks/useDeepAgentSession.ts`、`frontend/src/lib/api.ts`。

## 问题三：没有用户身份，按目录限制不足以隔离任务与文件

现象与原因：原项目没有账号或会话所有者，知道会话 ID 或输出路径可能访问其他人的任务。前端隐藏不能替代后端验证。

解决方法：

- 增加账号注册、登录和退出，保存密码哈希和带过期时间的服务器端登录会话。
- 后端为会话记录所有者，统一验证任务、事件连接、上传、文件列表和下载权限。
- 解析路径后检查会话边界，限制模型文件工具在当前工作区访问。
- 会话 ID 限制为小写字母、数字及允许符号，防止 Windows 文件系统大小写导致不同 ID 指向同一目录。
- 当前会话 ID 按账号分别保存；旧历史未确认归属，不自动认领。

修改位置：`app/api/auth.py`、`app/api/ownership.py`、`app/api/server.py`、`app/utils/path_utils.py`、`frontend/src/AuthGate.tsx`、`frontend/src/main.tsx`、`frontend/src/lib/thread.ts`。部署配置记录在 `frontend/README.md`。

## 开发中遇到的验证问题

- Vite 构建最初受沙箱目录读取限制；使用获准的构建执行方式后生产构建成功。
- Windows 测试清理临时数据库时出现文件占用；修正账号数据库连接上下文，确保提交后关闭连接。
- 8 项后端测试及前端历史恢复测试通过，覆盖离线存储、刷新回放、取消、重启中断、账号与文件隔离及路径边界。
- 自动化环境没有可用浏览器，未完成截图及点击验证。

## 已知边界

- 浏览器刷新可继续观察任务；后端重启后不能从中断步骤继续执行，Agent 检查点仍在内存。
- 旧会话及文件需要确认所有者才能迁移。
- 完整备份需包含两份 SQLite 数据库及 `app/output`、`app/updated` 下的会话文件。
