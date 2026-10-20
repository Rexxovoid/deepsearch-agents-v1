# 项目更新记录

按北京时间日期倒序汇总更新内容。问题定位及解决过程记录在本目录的 `YYYY-MM-DD-主题.md` 中。

## 2026-10-20

### 扩充评测任务集

- 默认任务从 12 项扩充至 32 项，搜索、SQL、知识库、文件各 8 项；新增快速检查集和四份分类任务集，支持现有 `--tasks` 参数独立运行。
- 补充知识库基准资料，以及 SQL 关联/分组/空结果、中文与长文档文件生成等场景；同步 README 和评测说明。
- 涉及文件：`evaluations/tasks.json`、`evaluations/suites/`、`evaluations/knowledge.md`、`tests/test_evaluations.py`、`README.md`、`docs/evaluation.md`。
- 验证：32 项离线任务通过，8 个文件任务真实生成 Markdown，其中 5 项进一步生成并解析 PDF；11 项评测回归通过，参考 SQL 校验及任务集一致性通过。
- 限制：未执行真实服务在线质量评测；RAG 需重新导入基准资料，默认集变化需重建基线。
- 开发日志：[任务集扩充与验证](2026-10-20-evaluation.md)。

### 四类任务评测体系

- 新增搜索、SQL、知识库回答、文件生成共 12 项起始任务，以及离线/在线 CLI、单项硬超时、JSON/Markdown 报告与兼容基线对比。
- 搜索报告排序与召回指标；SQL 比较问题生成查询和参考查询的执行结果；知识库检查基准事实及拒答；文件实际生成并解析验证。
- 涉及文件：`evaluations/`、`tests/test_evaluations.py`、`.env.example`、`.gitignore`、`README.md`、`docs/evaluation.md`；关键流程附中文注释。
- 验证：61 项后端测试通过；12 项离线评测通过，含实际 Markdown/PDF 生成；再次运行基线对比通过。
- 限制：搜索/SQL/RAG 的离线结果仅验证评分流程，真实服务质量未评测；知识库评分是规则代理指标，文件不包含视觉质量验收，也不代表完整 Agent 端到端质量。
- 使用说明：[评测体系](../evaluation.md)；开发日志：[问题与验证过程](2026-10-20-evaluation.md)。

### README 同步当前功能与结构

- 重写 README：以现有多智能体、账号隔离、历史会话、事件持久化、SQL 安全与队列为主体，同步目录、接口、配置、启动、数据保存和验证说明。
- 移除教程章节和学习宣传，简要注明原作者提供适合学习的配套资料；修正将已实现能力列为缺失功能的过时描述。
- `AGENTS.md` 增加后续功能修改在关键逻辑处添加简洁中文注释的约定。
- 涉及文件：`README.md`、`AGENTS.md`。验证：文档与代码、路径及配置核对；仅文档修改，未运行应用测试，未重新验收外部服务。
- 开发日志：[README 同步记录](2026-10-20-readme.md)。

### Docker Compose 启动路径修正

- 修复 README 将 Compose 文件误写为 `docs/docker-compose.yaml` 的问题，实际路径为 `docker/docker-compose.yaml`。
- 统一 README 和 Compose 顶部说明：从项目根目录运行，显式加载根目录 `.env`，补充状态与端口核对命令。
- 涉及文件：`README.md`、`docker/docker-compose.yaml`；验证为文档与实际路径核对，无需应用测试，未重启或重建数据库容器。
- 开发日志：[启动路径排查](2026-10-20-docker-compose.md)。

### 销售表查询连接失败修复

- 确认本机 MySQL 容器实际发布端口为 3306，而 `.env` 指向 3307，导致列举表名前连接即被拒绝（2003 / Windows 10061）；本地配置已改为 3306。
- SQL 重试日志与耗尽提示保留错误码及受控原因，连接错误提示核对端口映射；不回显驱动原始异常内容。
- 涉及文件：本地忽略的 `.env`、`app/tools/db_tools.py`、`tests/test_sql_safety.py`。
- 验证：16 项 SQL 测试通过；实际工具列举三张表成功，销售表前 40 条返回成功，重试次数 0。
- 限制：正在运行的后端与独立 worker 需重启加载配置；未执行浏览器模型对话验收。
- 开发日志：[定位证据与解决方法](2026-10-20-sql-query.md)。

### 持久化任务队列

- 后台执行从 API 内存任务改为 SQLite 持久化队列和独立 worker；默认自动启动，无需额外 Redis。
- 增加 FIFO 排队、全局并发上限、自动重试及退避、独立进程硬超时、取消、心跳租约和崩溃恢复；默认并发 2、每次 600 秒、最多额外重试 3 次。
- 增加任务状态与失败重试接口，沿用账号归属校验；前端显示排队、执行、等待重试、取消及手动重试。
- 队列和事件复用 `events.sqlite3`；跨进程进度推送按 ID 去重，旧尝试令牌隔离晚到结果，对话消息从已完成历史重建。

主要文件：`app/task_queue.py`、`app/worker.py`、`app/api/server.py`、`app/api/event_store.py`、`app/api/monitor.py`、`app/agent/main_agent.py`，以及前端会话/历史/状态组件和测试。

验证：50 项后端测试通过，含实际独立进程执行、崩溃重试、超时终止、自动 worker 重启和跨进程 WebSocket；前端历史恢复、滚动回归、TypeScript/生产构建、依赖锁文件检查通过。

限制：失败恢复会重新执行整项任务，不是 Agent 断点续跑，也不保证外部副作用恰好一次；仅支持同机本地磁盘队列。当前自动化使用替代模型任务，真实模型和浏览器交互需额外验收。

使用说明：[任务队列](../task-queue.md)；开发日志：[问题与实现过程](2026-10-20-task-queue.md)。

## 2026-10-19

### 历史阅读时保持页面位置

- 主对话和执行过程列表改为仅在底部跟随新消息，向上翻阅时暂停自动滚动，避免新事件或轮询更新将页面拉回底部。
- 增加“回到最新”按钮；滚回底部、点击按钮或发送新问题后恢复跟随，切换会话重新定位最新消息。
- 增加排队滚动回调取消和执行前状态检查，避免用户滚动与自动跳转竞争。

修改文件：`frontend/src/hooks/useFollowLatest.ts`、`frontend/src/App.tsx`、`frontend/src/components/ConversationThread.tsx`、`frontend/src/styles.css`、`tests/test_scroll_follow.cjs`。

验证：滚动模拟回归测试、历史恢复测试、TypeScript 和生产构建通过；未完成真实浏览器交互验证。

开发日志：[滚动问题与解决](2026-10-19-scroll.md)。

### SQL 安全与超时重试

- 新增默认表名白名单 `drugs,inventory,sales_records`，列表和预览仅访问允许的基础表；SQLGlot 校验所有 SELECT、CTE、JOIN、子查询和 UNION 中的真实表引用。
- 拒绝写操作、多语句、跨库查询、视图、文件导出、锁、用户变量、注释及非批准函数；执行重建的只读 SQL。
- 增加只读事务、查询与网络超时、结果行数限制；仅超时及临时故障自动重试，最多 3 次，同一研搜任务共享预算，耗尽后停止 SQL 访问。
- 新增 `MYSQL_ALLOWED_TABLES`、`MYSQL_QUERY_TIMEOUT_SECONDS`、`MYSQL_MAX_ROWS` 示例配置，更新 SQLGlot 和 MySQL 驱动依赖。

主要文件：`app/tools/sql_safety.py`、`app/tools/db_tools.py`、`app/agent/main_agent.py`、`app/prompt/prompts.yml`、`tests/test_sql_safety.py`，及依赖/示例配置文件。

验证：32 项后端测试（含新增 15 项 SQL 测试）、前端历史恢复测试、依赖锁文件和差异检查通过；真实 MySQL 白名单列举与只读 SELECT 成功，1 秒时限触发错误码 3024。本次未变更数据库账号权限，未提交或推送到 GitHub。

配置说明：[SQL 安全](../sql-security.md)；开发日志：[问题与解决过程](2026-10-19-sql-security.md)。

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
