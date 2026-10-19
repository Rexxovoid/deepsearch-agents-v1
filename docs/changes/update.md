# 项目更新记录

按北京时间日期倒序汇总更新内容。问题定位及解决过程记录在本目录的 `YYYY-MM-DD-主题.md` 中。

## 2026-10-19

### GitHub 上传准备与项目来源说明

- 根据用户终端日志定位 `dubious ownership` 为沙箱账号与 Administrator 的所有权差异，在上传教程补充仅信任此项目目录的处理步骤；等待用户执行验证。
- 将个人学习资料目录 `docs/knowledge_base/` 加入忽略规则，文件继续保留在本地；核验该目录未被 Git 跟踪且忽略规则生效。
- 补充 `.gitignore`：环境配置变体、运行工作区、数据库和日志、缓存及本地凭据，保留源码、锁文件和安全示例配置。
- 新增占位值 `.env.example`，将含本地配置的 `.envexamples` 排除在提交外。
- README 顶部注明基于 `didilili/deepsearch-agents` 二次开发，说明原作者和扩展功能，项目名使用 `deepsearch-agents-v1`。
- 新增 [GitHub 完整上传流程](../github-upload.md)，说明初始化、暂存、提交、建仓库、推送及验证。
- 用户已创建公开仓库 `Rexxovoid/deepsearch-agents-v1`，README 和教程已补充实际仓库地址；继续准备首次提交和推送。

验证：已检查忽略规则并扫描待提交文件，修正 README 两处失效截图引用，未变更应用代码。已初始化本地仓库；暂存被沙箱阻止，提权审批因服务容量不足未执行，因此首次提交和远程推送尚未完成。后续结果在开发日志中补充。

开发日志：[首次 GitHub 上传](2026-10-19-github.md)。

### 持久化、会话管理与账号权限

本节汇总此前已实现的持久化和本次开发补充的功能，不代表全部首次实现于当天。

- 前端保存当前会话 ID，刷新后重连原会话；当前会话选择按账号分别保存。
- Agent 在后端独立运行，刷新、切换会话或退出登录不会主动取消任务。
- SQLite 按会话保存有序事件；WebSocket 重连时回放历史，恢复问题、过程、结果和运行状态，使用事件 ID 去重。
- 支持断线重连，重新加载上传附件名称和生成文件列表。
- 隐藏侧栏会话 ID，增加可切换的历史会话列表，以用户第一个问题命名，后续追问不改名。
- 增加账号密码注册、登录及退出。密码使用带盐 scrypt 哈希，登录使用 HttpOnly Cookie。
- 后端校验会话列表、任务、WebSocket、附件及生成文件的所有权，防止跨用户访问、路径遍历和 Windows 路径大小写绕过。

主要文件：

- 后端：`app/api/event_store.py`、`app/api/monitor.py`、`app/api/server.py`、`app/api/auth.py`、`app/api/ownership.py`、`app/utils/path_utils.py`。
- 前端：`frontend/src/AuthGate.tsx`、`frontend/src/App.tsx`、`frontend/src/main.tsx`、`frontend/src/hooks/useDeepAgentSession.ts`、`frontend/src/lib/thread.ts`、`frontend/src/lib/history.ts`、`frontend/src/lib/api.ts`、`frontend/src/styles.css`。
- 测试：`tests/test_event_persistence.py`、`tests/test_ownership.py`、`tests/test_history.cjs`。

验证：此前前端生产构建、8 项后端测试及前端历史恢复测试通过。环境无可用浏览器，未完成视觉检查。

限制：后端重启保留历史，但运行中任务会中断，Agent 检查点仍在内存中。旧历史没有可靠所有者，不自动分配给新账号。备份应同时保留 `app/data/accounts.sqlite3`、`app/data/events.sqlite3` 及会话文件。

开发日志：[持久化与会话权限](2026-10-19-persistence.md)。

### RAGFlow 调用与诊断修复

- 兼容提问接口和助手列表格式，增加配置检查、请求超时、业务错误识别及异常路径临时会话清理。
- 拦截纯问号等无有效文字的输入，并提示检查编码。
- 纠正排查误判：PowerShell 管道使中文诊断问题变成问号，触发检索断言；保留正确中文后，实际检索及工具问答成功，不能据此判定模型或索引损坏。

主要文件：`app/tools/ragflow_tools.py`、`app/ragflow/rag_config.py`、`tests/test_ragflow_tools.py`。

验证：17 项后端回归测试通过，含 9 项 RAGFlow 测试；另行完成真实中文检索和工具问答。用户网页完整流程仍需重启后端后复测。

开发日志：[RAGFlow 定位与解决](2026-10-19-ragflow.md)。

### 文档维护约定

- 新增根目录 `AGENTS.md`，要求每次修改维护本更新记录及按日期命名的开发日志，并向用户提供修改位置。
- 补充持久化日志，整理 RAGFlow 日志，区分最终结论与已纠正的误判。

验证：仅文档修改，核对内容、文件路径和相对链接，未重复运行应用测试。
