# WebSocket 事件持久化与刷新恢复

后端将监控事件写入 `app/data/events.sqlite3`（SQLite），再尝试推送给前端。
无须新增服务或修改 MySQL 配置；数据库文件与生成文件分开放置，不会进入报告下载列表。
此目录已加入 `.gitignore`。保留此目录即可保留历史事件。

## 工作流程

1. `POST /api/task` 保存 `task_started` 事件，包含用户问题。
2. 工具调用、助手调用、工作目录、结果、错误和取消事件按 `thread_id` 保存，每条包含唯一递增 `id`。
3. 前端连接 `/ws/{thread_id}` 后，首先收到 `event_history` 消息：`events` 为完整历史，`is_running` 为后端实际运行状态。
4. 前端按 `task_started` 拆分对话轮次，恢复问题、执行过程、结果和工作目录，然后重新查询文件列表。
5. 后续实时事件继续传输；前端按事件 `id` 去重，避免重连时重复显示。

会话 ID 已保存在浏览器 localStorage 中，因此同一浏览器刷新会恢复同一会话。
“新建研搜”生成新 ID，显示空会话，不删除原有历史。
心跳 `pong` 不保存。

## 手动验证

1. 保持前后端运行，提交一个数据库统计或联网搜索任务。
2. 执行过程中刷新页面：原问题、已有事件和“研搜中”状态应恢复，后续事件继续出现。
3. 等待完成后再次刷新：最终答案应保留；如果生成了文件，文件列表应重新加载。
4. 在同一会话继续提问，完成后刷新：两轮问题和答案应分别显示。
5. 新建研搜：应出现空会话。
6. 执行中停止后端，再重新启动：历史保留，未完成任务显示中断提示，可以重新提交。

事件恢复不等于任务续跑：后端重启后，Agent 任务和内存检查点不会自动恢复。
该实现适用于当前单个 Uvicorn 工作进程；运行状态仍来自进程内任务表，不支持多进程任务协调。
功能启用前已经丢失的事件无法补回。本功能不恢复尚未上传的本地附件或输入框草稿。

## 自动验证

在项目根目录运行（不访问模型、MySQL、Tavily 或 RAGFlow）：

```powershell
uv run python -m unittest discover -s tests -v
node tests/test_history.cjs
```

前端构建：

```powershell
cd frontend
npm.cmd run build
```

若受限运行环境阻止默认配置加载器读取父目录，可使用：

```powershell
npm.cmd run build -- --configLoader runner
```
