# 改造计划：通用多 Agent 任务执行平台 → 跨境电商运营中台

> 改造对象：开源项目 Multi Agent Orchestration（MIT，通用多 Agent 任务执行平台）
> 技术栈保持不变：Python / FastAPI / LangGraph / PostgreSQL / Redis Pub/Sub / Docker 沙箱 / WebSocket
> 约束：纯 Python 后端，不引入 Java/Go/C++（沙箱执行器保留，但不作为业务依赖）

---

## 一、现状盘点

| 层 | 现状 | 与目标差距 |
|----|------|-----------|
| Agent | `graph/agents/` 下 `planner / researcher / coder / critic / github_agent`（通用） | 需替换为 5 个跨境电商专业 Agent + 运营主管 |
| 编排 | `supervisor.py` 固定路由 4 个通用节点；`build_graph.py` 无 checkpointer | 需改为「目标→依赖 DAG→领域路由→汇总」，并支持 `interrupt()` |
| 状态 | `state.py` 为通用 `goal/plan/results` | 需扩展任务类型、DAG、审批、执行记录等字段 |
| 工具 | `tools/` 仅 `web_search / rag_retriever / code_executor / ocr / vision` | 需新增 13 个领域工具（含 Pydantic IO schema） |
| 数据库 | `users`（auth.py 内建）、`agent_runs`（postgres_memory.py 内建），其余表假设已存在 | 需集中建表脚本 + 6 张领域业务表 |
| Redis | `bus/redis_bus.py` 频道 `run:{run_id}`，事件无类型 | 需定义 `task.*/agent.*/approval.*` 事件规范 |
| API | `POST /run`、`/plan`、`/apikeys`… | 需新增 `/tasks`、`/approvals`、`/agents` |
| WS | `ws/stream.py` 仅转发 `run:{id}` | 需支持任务/审批事件流 |
| 配置 | `config.py`；**`.env.example` 缺失**（README 却引用） | 需新建 `.env.example` 并补领域配置 |
| 测试 | **无 tests 目录** | 需新增 pytest 用例 |
| 文档 | README 为通用多 Agent 平台说明 | 需重写为运营中台 + 架构图/审批流程图 |

> ⚠️ 现存可运行性缺陷（非本次业务改造引入，但会影响「确保可运行」）：
> 1. `services/db.py`、`services/preferences.py`、`graph/agents/researcher.py` 均以 `ssl="require"` 直连，**连不上本地 Docker Postgres**；
> 2. `graph/agents/coder.py` 硬编码 Windows 绝对路径（`C:\...`）；
> 3. `bus/redis_bus.py` 强制把 `redis://` 改写为 `rediss://`；
> 4. `graph/build_graph.py` 的 `GRAPH` 无 checkpointer，`interrupt()` 无法工作。

---

## 二、目标架构

```
运营目标(自然语言/结构化)
        │
        ▼
┌────────────────────────────┐
│  运营主管 Agent (Supervisor) │  ①目标→依赖 DAG  ②路由  ③汇总  ④异常决策
└───────────┬────────────────┘
            │ 带依赖的 DAG（含 assigned_to / depends_on / priority）
            ▼
 ┌──────────┬───────────┬────────────┬──────────┬────────────┐
 │选品分析   │订单与物流 │多语言客服  │定价       │数据周报     │
 │selection │order_log. │cs_service  │pricing   │weekly_report│
 └────┬─────┴─────┬─────┴─────┬──────┴────┬─────┴──────┬─────┘
      │           │           │           │            │
      └───────────┴─────领域工具集(tools/domain)────────┘
                          │
              ┌───────────┴────────────┐
              ▼                        ▼
   ┌──────────────────┐      ┌───────────────────┐
   │ 审批护栏(HITL)     │      │ Redis Pub/Sub 事件 │
   │ interrupt+审批矩阵│      │ → WebSocket 前端   │
   └────────┬─────────┘      └───────────────────┘
            ▼
     PostgreSQL 持久化(任务/DAG/执行/审批/业务表)
```

**流程**：Supervisor 收到运营目标 → 产出依赖 DAG → 按 DAG 顺序路由到领域 Agent → 领域 Agent 调用工具 → 高风险操作触发审批护栏暂停 → 人工审批 → 恢复或取消 → 全部完成由 Supervisor 汇总。

---

## 三、文件清单与改动点

### A. Agent 层（`backend/graph/agents/`）

**新增 5 个领域 Agent（每个文件含：职责边界、工具集、决策依据、失败降级策略）**

| 新文件 | 职责 | 主要工具 | 降级策略 |
|--------|------|----------|----------|
| `selection_agent.py` 选品分析 | 市场调研/竞品分析/利润测算/选品建议 | `market_data`、`competitor_price`、`fx_rate`、`profit_calculator` | 市场数据缺失→改用本地样例+置信度降级；无汇率→用缓存汇率并标注 |
| `order_logistics_agent.py` 订单与物流 | 订单查询/物流异常分类/补发退款决策 | `order_api`、`logistics_api`、`inventory_api`、`refund_api`(需审批) | 物流查询超时→重试1次→标记待人工；退款失败→转审批/人工 |
| `customer_service_agent.py` 多语言客服 | 工单分类/情感分析/回复草稿 | `ticket_api`、`translation`、`db_query`(知识库) | 翻译失败→保留原文+标注；工单拉取失败→跳过并记录 |
| `pricing_agent.py` 定价 | 依库存/销量/竞品动态定价 | `pricing_engine`、`competitor_price`、`inventory_api`、`pricing_history` | 缺竞品价→按规则保底价；改价超阈值→强制审批 |
| `weekly_report_agent.py` 数据周报 | 汇总运营数据生成周报 | `db_query`、`charts`(生成图表文件)、`notify`(推送) | 数据缺失→在报告中标注缺口；推送失败→落库待重发 |

**重写**
- `planner.py` → 运营 DAG 规划器：输出 `[{id, task, assigned_to, depends_on, priority, requires_approval}]`，`assigned_to` 取值改为 `selection|order_logistics|customer_service|pricing|weekly_report`。
- `supervisor.py` → 运营主管：目标分解 + 条件路由 + 依赖管理 + 结果汇总 + 异常处理（重试/降级/转人工）。
- `critic.py` → 保留并改造为「运营结果校验 Agent」（校验数据完整性、建议合规性、审批是否闭环）。
- `researcher.py` / `coder.py` / `github_agent.py` → 视决策保留（支持"保留双模式"时）或停用。

### B. 编排层（`backend/graph/`）

- `state.py`：新增字段 `task_id、task_type、dag、priorities、approvals、pending_approval、executed_ops、agent_trace、hitl_enabled`。
- `build_graph.py`：注册新节点；挂载 **checkpointer**（Postgres/Redis/Memory）；加入「审批节点 → 条件边（approved→继续 / rejected→终止）」，支持 `interrupt()` 暂停与恢复。
- `router.py`：`/planner /selection /logistics /cs /pricing /report` 等 slash 命令；按任务关键词/类型路由。
- **新增** `approval.py`：审批护栏节点 + 审批矩阵 + `interrupt()` 逻辑 + 审批记录写入。
- `rag_flow.py`：保留（客服知识库检索复用）。

### C. 工具层（**新增** `backend/tools/domain/`）

- `schemas.py`：所有工具 I/O 的 Pydantic 模型（`*Input` / `*Output`）。
- `registry.py`：工具注册表，标注「是否需要审批」「审批类型」「超时/重试策略」。
- 13 个工具模块：`market_data.py、competitor_price.py、fx_rate.py、profit_calculator.py、order_api.py、logistics_api.py、inventory_api.py、refund_api.py、translation.py、ticket_api.py、pricing_engine.py、notify.py、db_query.py`
  - 每个工具统一 `invoke(input) -> output`，含超时、重试、缺失降级、权限/审批标记。
- 保留 `web_search.py / rag_retriever.py / code_executor.py`（沙箱执行器保留但不承载业务；`executors/` 目录保留）。

### D. 数据层

- **新增** `backend/services/schema.sql`：集中建表（`users/sessions/user_api_keys/user_preferences/cached_runs/activity_logs` 迁移至此 + 新增领域表）。
- **新增领域表**：`operations_tasks`、`agent_executions`、`approvals`、`order_exceptions`、`shipments`、`pricing_history`、`support_tickets`、`product_candidates`、`notifications`。
- `memory/postgres_memory.py`：改为加载 `schema.sql` 并新增领域表 CRUD。
- **新增** `backend/bus/events.py`：事件类型常量与发布辅助（`task.created / agent.started / agent.completed / approval.requested / approval.granted / approval.rejected / task.failed`）。
- `bus/redis_bus.py`、`ws/stream.py`：频道与事件结构扩展（支持任务与审批事件）。

### E. API 层（`backend/routes/`）

- **新增** `tasks.py`：`POST /tasks`、`GET /tasks/{task_id}`、`GET /tasks/{task_id}/agents`。
- **新增** `approvals.py`：`GET /approvals`、`POST /approvals/{approval_id}/approve`、`POST /approvals/{approval_id}/reject`。
- **新增** `agents.py`：`GET /agents`（列出领域 Agent 及状态）。
- `main.py`：注册新路由；`/run` 保留兼容或迁移为 `/tasks`；WebSocket 支持任务/审批频道。
- 保留 `auth / apikeys / admin / plan / tools / uploads ...` 不变。

### F. 配置与示例数据

- **新增** `.env.example`（当前缺失）：跨境配置（各领域 API Key、`APPROVAL_THRESHOLD_*`、`AGENT_MAX_CONCURRENCY`、`HITL_ENABLED`、`MOCK_ENABLED` 等）。
- `backend/config.py`：新增审批阈值（美元分档）、并发数、HITL 开关、领域数据源开关。
- **新增** `backend/sample_data/*.json`：订单、物流、库存、竞品价格、工单、选品候选、日销数据。
- **新增** `backend/seed_demo.py`：把示例数据灌入 Postgres，供演示。

### G. 测试（**新增** `backend/tests/`）

- `test_supervisor_dag.py`：目标→DAG 分解与依赖顺序。
- `test_approval_guardrail.py`：审批矩阵分档、`approve/reject` 流转、记录落库。
- `test_domain_tools.py`：各工具 IO schema 与降级路径。
- `test_api_tasks.py`：`/tasks`、`/approvals`、`/agents` 接口。
- `conftest.py`：sqlite/in-memory 或 testcontainer 夹具。

### H. 文档

- 重写 `README.md`：项目定位、架构图、Agent 角色表、技术栈、运行步骤、示例任务、审批矩阵。
- **新增** `docs/ARCHITECTURE.md`（Agent 协作流程说明 + 流程图）。
- **新增** `docs/APPROVAL_FLOW.md`（审批流程说明 + 审批矩阵）。
- **新增** `docs/DIAGRAMS.pdf`（全部流程图，矢量 PDF；已替代文档中的 mermaid 代码块）。
- 关键业务代码补中文注释。

### I. 前端

本次改造**仅覆盖后端与文档**，前端控制台未纳入范围（已从仓库移除）；接口与 WebSocket 事件均已预留，
可另行对接前端：`POST /tasks`、`GET /tasks/{id}`、`GET /approvals`、`POST /approvals/{id}/approve|reject`、`GET /agents`、`WS /ws/{run_id}`。

---

## 四、新增文件汇总（约 35 个）

```
backend/graph/agents/{selection_agent,order_logistics_agent,customer_service_agent,pricing_agent,weekly_report_agent}.py
backend/graph/approval.py
backend/tools/domain/{schemas,registry,market_data,competitor_price,fx_rate,profit_calculator,order_api,logistics_api,inventory_api,refund_api,translation,ticket_api,pricing_engine,notify,db_query}.py
backend/services/schema.sql
backend/bus/events.py
backend/routes/{tasks,approvals,agents}.py
backend/sample_data/*.json
backend/seed_demo.py
backend/tests/{conftest,test_supervisor_dag,test_approval_guardrail,test_domain_tools,test_api_tasks}.py
.env.example
docs/{ARCHITECTURE,APPROVAL_FLOW}.md
```

---

## 五、风险与待确认事项

1. **外部系统全部缺失**：Shopee/Lazada/Amazon、物流、翻译、工单、汇率均无真实 API。工具的数据源策略必须先定（Mock / 真实适配器 / 混合）。
2. **HITL 需要引入 checkpointer**：当前图无 checkpointer，`interrupt()` 无法工作。启用后需新增「恢复执行」接口（`POST /tasks/{id}/resume` 或审批后自动恢复），这是本次最大的架构变动。
3. **LangGraph 版本**：`langgraph==0.2.4` 较旧，`interrupt/checkpointer` API 需实测；必要时在保持技术栈前提下小幅升级版本。
4. **本地可运行性缺陷**：上面 4 处（`ssl="require"`、硬编码路径、`rediss://`）是否一并修复。
5. **改造范围**：仅后端，还是后端 + 前端界面。
6. **遗留功能**：桌面端、GitHub 集成、管理后台等与业务无关的模块是否保留（本次处理：移除桌面端与桌面端专属路由，其余保留未接入编排）。
7. **数据与合规**：示例数据是否使用虚构品牌/订单即可，避免真实 PII。
```
