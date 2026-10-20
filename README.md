# 深度研搜 · deepsearch-agents-v1

基于 DeepAgents 的对话式多智能体研究系统，支持网络搜索、MySQL 只读查询、RAGFlow 知识库问答、附件读取和 Markdown/PDF 报告生成，并提供账号隔离、历史会话、事件持久化及后台任务队列。

## 项目来源

本项目基于 [didilili/deepsearch-agents](https://github.com/didilili/deepsearch-agents) 二次开发，原作者为 [didilili](https://github.com/didilili)。原项目提供多智能体调度、工具接入及前后端通信等基础能力，感谢原作者的工作。

原作者提供配套学习文档与章节示例，适合学习 DeepAgents 和多智能体应用开发，详见原仓库及 [ai-agents-from-zero](https://github.com/didilili/ai-agents-from-zero)。本 README 说明扩展版本当前的功能、结构和运行方式；本仓库不是原作者官方版本。

## 当前功能

| 功能 | 当前行为 |
| --- | --- |
| 多智能体研搜 | 主智能体规划与汇总，网络搜索、数据库、RAGFlow 三个助手按需检索 |
| 文件处理 | 读取上传的 PDF、Word、Excel、Markdown 和文本，生成 Markdown/PDF 文件 |
| 账号与访问控制 | 账号密码注册、登录和退出；校验会话、任务、WebSocket、上传及生成文件的归属 |
| 历史会话 | 左侧展示会话列表，以用户第一个问题命名；切换和刷新后恢复历史 |
| 实时进度 | WebSocket 推送执行事件，断线后重连并回放持久化事件 |
| 历史阅读 | 向上阅读时保持位置，支持回到最新消息 |
| 后台队列 | SQLite 持久化排队，独立 worker 执行；支持并发控制、重试、超时、取消和故障恢复 |
| SQL 安全 | 表名白名单、只读 SELECT 校验、结果行数上限、超时和有限重试 |
| RAGFlow 接入 | 助手发现、临时会话问答、配置校验、超时和业务错误处理 |

刷新浏览器不会主动取消后台任务。刷新恢复依赖已保存的任务和事件；worker 失联后按租约及重试预算重新执行任务，**不是恢复 Agent 中间步骤**。

## 执行流程与技术栈

```text
浏览器（React / Vite / Ant Design）
  → FastAPI：登录与归属校验、上传文件、提交任务
  → SQLite：保存任务队列和会话事件
  → 独立 worker：领取任务，为每次尝试启动执行进程
  → 主智能体（DeepAgents / LangGraph / LangChain）
      ├─ 网络搜索助手 → Tavily
      ├─ 数据库助手 → MySQL + SQLGlot 只读校验
      ├─ 知识库助手 → RAGFlow
      └─ 文件工具 → 附件读取、Markdown/PDF 生成
  → 持久化事件 → FastAPI / WebSocket → 浏览器进度、结果和文件
```

模型通过 OpenAI 兼容接口接入。文件处理使用 pypdf、python-docx、pandas 和 ReportLab。账号与队列使用本地 SQLite，业务查询数据存放于 MySQL，两者用途不同。

## 项目结构

以下列出主要模块；运行时数据目录不提交 Git。

```text
deepsearch-agents-v1/
├── app/
│   ├── agent/
│   │   ├── subagents/             # 网络、数据库、RAGFlow 助手
│   │   ├── llm.py                 # 模型初始化
│   │   └── main_agent.py          # 主智能体与任务执行入口
│   ├── api/
│   │   ├── server.py              # HTTP / WebSocket、队列集成
│   │   ├── auth.py                # 账号、密码哈希和登录会话
│   │   ├── ownership.py           # 会话归属与访问校验
│   │   ├── event_store.py         # SQLite 事件持久化
│   │   ├── monitor.py             # 进度事件与连接管理
│   │   └── context.py             # 当前会话上下文
│   ├── prompt/prompts.yml         # 主智能体与助手提示词
│   ├── tools/
│   │   ├── db_tools.py            # MySQL 查询、超时与重试诊断
│   │   ├── sql_safety.py          # SQL AST 校验与白名单策略
│   │   ├── ragflow_tools.py       # 知识库助手调用
│   │   ├── tavily_tool.py         # 网络搜索
│   │   └── …                      # 附件读取、Markdown/PDF 工具
│   ├── ragflow/                   # RAGFlow 配置与调用示例
│   ├── utils/                     # 路径处理、文件转换
│   ├── task_queue.py              # 持久化队列、状态与租约
│   ├── worker.py                  # 领取任务、子进程执行与超时终止
│   ├── data/                      # 运行时：accounts.sqlite3、events.sqlite3
│   ├── updated/                   # 运行时：会话上传附件
│   └── output/                    # 运行时：会话生成文件
├── frontend/
│   ├── src/
│   │   ├── App.tsx                # 会话列表、对话与任务操作
│   │   ├── AuthGate.tsx           # 登录和注册界面
│   │   ├── components/            # 对话、文件和进度组件
│   │   ├── hooks/                 # 会话连接与滚动跟随
│   │   └── lib/                   # API、历史重建和配置
│   ├── package.json
│   └── vite.config.ts             # 前端端口与开发代理
├── docker/
│   ├── docker-compose.yaml        # MySQL 容器与持久化数据卷
│   └── mysql/mysql.sql            # 药品、库存、销售样例数据
├── docs/
│   ├── changes/                   # 更新记录与按日期编写的开发日志
│   ├── event-persistence.md       # 持久化与恢复说明
│   ├── task-queue.md              # 队列配置、恢复边界与验收
│   └── sql-security.md            # SQL 安全策略
├── tests/                         # 后端、历史恢复与滚动行为测试
├── examples/                      # 原项目示例
├── .env.example                   # 配置模板，不含真实密钥
├── AGENTS.md                      # 项目协作与文档维护约定
├── pyproject.toml
├── requirements.txt
└── uv.lock
```

## 本地运行

需要 Python 3.12、uv、兼容前端依赖的 Node.js、npm，以及 Docker Compose（使用仓库提供的 MySQL 时）。准备支持工具调用的模型服务；网络搜索和知识库任务分别需要 Tavily 和 RAGFlow 服务。

以下命令以 Windows PowerShell 为例。

### 1. 获取代码并安装依赖

```powershell
git clone https://github.com/Rexxovoid/deepsearch-agents-v1.git
cd deepsearch-agents-v1
uv sync
Copy-Item .env.example .env
```

已有 `.env` 时保留原配置，按模板补充所需字段。

### 2. 配置服务

编辑根目录 `.env`，填写自己的配置；不要提交真实密钥。

| 配置组 | 主要变量 | 说明 |
| --- | --- | --- |
| 模型 | `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LLM_QWEN_MAX` | 模型接口、密钥和模型名称 |
| 网络搜索 | `TAVILY_API_KEY` | Tavily 密钥 |
| 知识库 | `RAGFLOW_API_URL`、`RAGFLOW_API_KEY`、`RAGFLOW_TIMEOUT_SECONDS` | 已部署的 RAGFlow 服务 |
| 数据库 | `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE` | 与实际数据库连接及容器映射保持一致 |
| SQL 策略 | `MYSQL_ALLOWED_TABLES`、`MYSQL_QUERY_TIMEOUT_SECONDS`、`MYSQL_MAX_ROWS` | 默认白名单 drugs、inventory、sales_records；10 秒、最多 1000 行 |
| 浏览器与登录 | `FRONTEND_ORIGINS`、`COOKIE_SECURE` | 本地允许的前端来源；HTTPS 部署时设置安全 Cookie |
| 任务队列 | `TASK_QUEUE_*` | 默认并发 2，每次尝试 600 秒，失败后最多额外重试 3 次 |

SQL 工具内部重试与任务队列重试是独立预算。详细规则见 [SQL 安全](docs/sql-security.md) 和 [任务队列](docs/task-queue.md)。修改 `.env` 后应重启后端及单独启动的 worker。

### 3. 启动 MySQL

在项目根目录执行，显式加载根目录 `.env`：

```powershell
docker compose --env-file .env -f docker/docker-compose.yaml up -d
docker compose --env-file .env -f docker/docker-compose.yaml ps
```

Compose 文件在 `docker/` 目录。模板使用 `MYSQL_PORT=3307`；现有环境应以实际端口映射为准，例如容器发布 3306 时，应用配置也需使用 3306。

MySQL 使用命名数据卷保存数据。`docker/mysql/mysql.sql` 仅在首次初始化空数据目录时导入样例数据；已有数据卷不会重复导入。常规停止可使用同样参数执行 `stop`；`down -v` 会删除数据卷。

RAGFlow 不由本仓库 Compose 启动，需自行部署或接入已有服务，并准备可用的知识库助手。

### 4. 启动后端

```powershell
uv run uvicorn app.api.server:app --host 0.0.0.0 --port 8000 --reload
```

开发接口地址为 `http://localhost:8000`，接口文档为 `http://localhost:8000/docs`。默认后端自动管理独立 worker，无需安装 Redis。

需要单独管理 worker 时，在 `.env` 设置 `TASK_QUEUE_AUTOSTART=0`，重启后端，并在另一个项目根目录终端运行：

```powershell
uv run python -m app.worker
```

API 与 worker 必须访问同一份队列数据库。`--reload` 用于开发；队列恢复规则见 [任务队列说明](docs/task-queue.md)。

### 5. 启动前端

新开终端，在项目根目录执行：

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

浏览器打开 **http://localhost:5173/**，注册或登录后即可创建研搜任务。5173 是前端开发端口，8000 是后端端口；前端端口配置在 `frontend/vite.config.ts`。端口占用时以 Vite 终端显示地址为准。

前端当前默认直接访问 `http://localhost:8000`，WebSocket 地址为 `ws://localhost:8000`，配置逻辑位于 `frontend/src/lib/config.ts`。Vite 同时配置了 `/api` 和 `/ws` 开发代理供相对路径请求使用。需要指定其他地址时，在 `frontend/.env.local` 配置并重启前端：

```dotenv
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_BASE_URL=ws://localhost:8000
```

前后端本地访问建议统一使用 `localhost`，配置不同来源时同步检查后端 `FRONTEND_ORIGINS`。更多前端说明见 [frontend/README.md](frontend/README.md)。

## 使用方式

1. 注册或登录账号，新建研搜任务，按需上传附件。
2. 输入问题，例如“查询销售表前 40 条记录”或“读取附件并整理研究摘要”。
3. 查看排队状态、助手执行进度、最终回答和生成文件。
4. 在左侧切换历史会话；刷新页面可重新加载已保存的历史及运行状态。
5. 可取消排队或运行任务；失败后使用“重试任务”重新入队。

## 主要接口

业务接口校验登录状态，会话资源还会校验所有者。

| 接口 | 用途 |
| --- | --- |
| `POST /api/auth/register`、`POST /api/auth/login` | 注册和登录 |
| `GET /api/auth/me`、`POST /api/logout` | 获取当前账号、退出 |
| `GET /api/conversations` | 当前用户的历史会话 |
| `GET /api/conversations/{thread_id}/uploads` | 本会话附件列表 |
| `POST /api/task` | 提交任务并持久化入队 |
| `GET /api/task/{thread_id}` | 最新任务状态、排队位置与错误 |
| `POST /api/task/{thread_id}/retry` | 重新提交失败任务 |
| `POST /api/task/{thread_id}/cancel` | 取消任务 |
| `POST /api/upload` | 上传会话附件 |
| `GET /api/files`、`GET /api/download` | 列出和下载生成文件 |
| `WebSocket /ws/{thread_id}` | 历史事件回放与实时进度 |

## 数据保存与恢复边界

| 数据 | 保存位置 |
| --- | --- |
| 用户、登录会话与会话归属 | `app/data/accounts.sqlite3` |
| 任务队列与执行事件 | `app/data/events.sqlite3` |
| 用户上传附件 | `app/updated/` 下的会话目录 |
| 生成文件 | `app/output/` 下的会话目录 |
| MySQL 业务数据 | Docker 命名数据卷，或自行配置的 MySQL 实例 |

账号支持跨浏览器登录后查看自己的会话。备份需同时保留两份 SQLite、会话文件和 MySQL 数据；SQLite 备份应协调停止写入，或使用一致性备份方式，避免遗漏 WAL 中的数据。

当前队列面向同一主机的本地磁盘，不是多服务器分布式队列。故障恢复会重新尝试整项任务，可能重复模型调用或文件生成；不保证外部副作用恰好执行一次。LangGraph 的中间检查点仍使用内存存储。当前权限以账号和资源归属为主，尚未提供角色管理、文件病毒扫描或完整生产监控。

## 验证修改

在项目根目录运行后端自动化测试和前端行为测试：

```powershell
uv run python -m unittest discover -s tests -p 'test_*.py'
node tests/test_history.cjs
node tests/test_scroll_follow.cjs
npm.cmd --prefix frontend run build
```

自动化测试包含替代数据库连接或替代模型执行，不代表真实外部服务已验证。修改数据库连接后，可额外实际测试表名查询：

```powershell
uv run python -c "from app.tools.db_tools import list_sql_tables; print(list_sql_tables.invoke({}))"
```

该命令会访问 `.env` 中配置的 MySQL。真实模型、Tavily、RAGFlow 和浏览器交互需按所修改功能单独验收。

## 项目文档

- [更新记录](docs/changes/update.md)：功能变更、验证结果及开发日志链接。
- [事件持久化](docs/event-persistence.md)：刷新、重连与历史恢复。
- [任务队列](docs/task-queue.md)：并发、重试、超时、取消与故障恢复。
- [SQL 安全](docs/sql-security.md)：白名单、只读校验与查询限制。
