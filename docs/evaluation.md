# 研搜评测体系

## 范围

评测代码位于 `evaluations/`，独立于用户会话和任务队列，不需要启动前后端。默认任务集为 `evaluations/tasks.json`，包含四类各 8 项，共 32 项；适合作为后续扩充的基准集，不代表全面质量结论。

## 选择任务集

`--tasks` 选择任务集，`--category` 可在该任务集中进一步筛选；每条命令均可独立执行，无需先运行离线模式。

| 文件 | 数量 | 用途 |
| --- | --- | --- |
| `evaluations/tasks.json` | 32 | 默认全量任务 |
| `evaluations/suites/smoke.json` | 12 | 保留最初的题目，快速检查基本链路 |
| `evaluations/suites/search.json` | 8 | 官方文档定位，包含 pathlib、MySQL、FastAPI、JSON、CSV、unittest、SQLite、日志 |
| `evaluations/suites/sql.json` | 8 | 前 40 行、计数、区域汇总、库存排序、中文筛选、关联、HAVING、空结果 |
| `evaluations/suites/rag.json` | 8 | 重试、恢复、未知价格、存储、刷新、归属、SQL 策略、并发与超时 |
| `evaluations/suites/file.json` | 8 | Markdown、PDF、表格、中文、列表、代码块及长文档 |

```powershell
uv run python -m evaluations.run --mode offline --tasks evaluations/suites/smoke.json
uv run python -m evaluations.run --mode live --tasks evaluations/suites/search.json
uv run python -m evaluations.run --mode live --tasks evaluations/suites/sql.json
uv run python -m evaluations.run --mode live --tasks evaluations/suites/rag.json
uv run python -m evaluations.run --mode offline --tasks evaluations/suites/file.json
```

知识库任务扩充后，需将更新的 `evaluations/knowledge.md` 重新导入专用知识库并完成解析。默认任务集已变化，旧全量报告不能直接作为新基线；先运行新任务集建立新基线。各独立任务集与默认集同步维护，回归测试检查内容一致。

| 类别 | 在线被测链路 | 指标与通过条件 |
| --- | --- | --- |
| 搜索 | 问题 → 项目 Tavily 工具 → 搜索结果 | Precision@K、Recall@K、MRR、nDCG@K；默认以 nDCG ≥ 0.6 通过 |
| SQL | 问题 → 项目配置的模型生成 SQL → SQL 安全工具执行 | 与参考 SQL 的 CSV 结果比较；列名及记录匹配才通过，SQL 错误不能算匹配 |
| 知识库 | 问题 → 项目 RAGFlow 工具 → 专用基准知识库回答 | 必需事实覆盖率、禁止表述命中数；默认事实全部覆盖且无禁止表述才通过 |
| 文件 | 项目 Markdown 工具 → 可选 PDF 工具 → 读取产物 | Markdown 内容一致；PDF 文件头、页面可解析、指定文本存在；全部满足才成功 |

这是工具与模型能力评测，不覆盖主智能体的完整规划、工具选择、浏览器或任务队列调度。SQL 使用仓库样例业务表结构，不是完整数据库子智能体评测；自定义其他业务库时需同步调整 `evaluations/run.py` 中提供给模型的表结构。

## 离线运行

在项目根目录执行：

```powershell
uv run python -m evaluations.run --mode offline
```

搜索、SQL、RAG 的离线模式读取任务中的固定 `fixture`，只检查评分与报告流程；文件类实际调用项目工具生成文件。离线 SQL 固定样本仅用于比较器校验，例如 sales-40 的 fixture 只有两行模拟值，**不代表数据库已查询 40 行**。离线满分不能作为真实搜索、数据库或知识库质量证据。

## 在线运行

读取项目根目录 `.env`，使用现有模型、Tavily、MySQL、RAGFlow 配置。在线调用可能产生模型和服务费用。

### 搜索

```powershell
uv run python -m evaluations.run --mode live --category search
```

默认使用官方文档页作为相关结果标注。新增 Python 页面已核对官方 [JSON](https://docs.python.org/3/library/json.html)、[CSV](https://docs.python.org/3/library/csv.html)、[unittest](https://docs.python.org/3/library/unittest.html)、[sqlite3](https://docs.python.org/3/library/sqlite3.html) 和 [logging](https://docs.python.org/3/library/logging.html) 文档地址。URL 比较去除跟踪参数、锚点和尾斜杠，仍匹配具体页面；重复 URL 只计一次相关命中。Precision@K 的分母固定为 K，返回不足 K 项不会自动补满得分。结果变化或官方文档 URL 调整后，应人工复核标注。

### 数据库

```powershell
uv run python -m evaluations.run --mode live --category sql
```

需 MySQL 可连接，存在 `drugs`、`inventory`、`sales_records`，并配置对应白名单。候选 SQL 和参考 SQL 均经过项目只读安全校验，不绕过策略、不导入或重建数据库。

比较列名、数值、重复行与排序；数字 20 和 20.00 视作等价。`ordered=true` 时严格按顺序比较，否则按包含重复项的记录集合比较。参考查询在当前数据库执行，不写死业务统计值。评测时应避免并发修改业务数据，以免前后两次查询读到不同状态。此方式衡量执行结果匹配，不证明 SQL 在所有数据集上语义等价。

### 知识库

1. 将 [基准资料](../evaluations/knowledge.md) 导入一个专用 RAGFlow 知识库并完成解析。
2. 创建唯一名称的聊天助手并绑定该知识库。
3. 在当前终端指定助手名称后执行：

```powershell
$env:EVAL_RAGFLOW_CHAT_NAME = '你的专用评测助手名称'
uv run python -m evaluations.run --mode live --category rag
```

也可在本地 `.env` 配置助手名称。工具会创建临时会话并尝试清理。任务包含事实问答、恢复边界和资料未提供信息时的拒答。

整项硬超时会直接终止进程，届时可能来不及执行临时会话清理；可在 RAGFlow 中检查并删除遗留的评测临时会话。

事实关键词和同义表述覆盖率只是回答质量代理指标：可能漏判正确改写，也可能被含有关键词但不正确的回答误导；禁止表述规则也不能理解所有否定语境。这里没有声称实现检索证据归因、引用真实性或完整忠实度评分。正式评测需人工抽查回答与原文，逐步补充同义表述及负例。

### 文件与全量评测

```powershell
uv run python -m evaluations.run --mode live --category file
uv run python -m evaluations.run --mode live
```

文件检查实际生成和可读性，不评价版面美观，也不评价模型写报告的论证质量。

## 报告、超时和基线

默认每次运行生成独立目录 `evaluations/reports/北京时间时间戳/`：

- `report.json`：模式、任务集 SHA256、单项分数、细项指标、错误、耗时与分类汇总。
- `report.md`：便于阅读的汇总和任务明细。
- `artifacts/`：本次实际生成的文件，按任务隔离，避免旧文件导致误判成功。

默认每项硬超时 120 秒；执行进程超时后会终止。错误和超时计作失败，不从通过率分母中剔除；未选择的类别显示“未评测”。报告中的细项平均分标注有效样本数，需结合错误数阅读。SQL 工具内有限重试仍有效，不另外自动重复整项评测。

```powershell
uv run python -m evaluations.run --mode offline --timeout 60 --output evaluations/reports
uv run python -m evaluations.run --mode offline --baseline evaluations/reports/某次运行目录/report.json
```

基线要求模式、任务集 SHA256 和选中任务一致，输出分类通过率差值。不兼容的基线会在调用外部服务前拒绝。修改模型、知识库或服务版本时应额外记录环境；相同任务集哈希并不代表外部环境相同。

任何任务未通过，CLI 返回退出码 1；全部通过返回 0，可接入后续 CI。非法参数或任务集直接报错。报告不保存原始 SQL 结果、知识库回答、服务密钥或原始异常文本，仅保存异常类型；详细排查需结合服务日志。默认报告目录已加入 `.gitignore`，自定义输出到其他目录时应自行检查提交范围。

## 扩充任务与验证评分器

复制现有任务并填写唯一安全 `id`、`category`、`threshold` 和对应标注。任务集版本当前为 1：

- 搜索：`question`、`relevant_urls`、`k`、离线 `fixture.results`。
- SQL：`question`、`reference_sql`、`ordered`、离线 `fixture.candidate/reference` CSV。
- RAG：`question`、`required_facts`（每项是一组同义表述）、`forbidden_phrases`、离线 `fixture.answer`。
- 文件：`format`（md/pdf）、`content`、`required_text`。

建议新增失败案例、边界问题和业务代表性任务；不要只增加容易获得满分的样例。知识库问题变化时同步更新基准资料，并保留不知道答案的问题。

```powershell
uv run python -m unittest discover -s tests -p test_evaluations.py -v
uv run python -m unittest discover -s tests -p 'test_*.py'
```

评分回归覆盖重复搜索结果、低排名与无关页面、SQL 错序/重复行/错误回包、缺失事实、禁止表述、错误报告、超时、文件内容缺失，以及基线模式/任务集不兼容。
