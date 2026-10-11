# CrossBorder Ops Agent · 跨境电商运营中台

![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Latest-green)
![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent-orange)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![WebSocket](https://img.shields.io/badge/WebSocket-Real--Time-green)

> 你给出一句运营目标，**运营主管 Agent** 会把它拆成依赖感知的 DAG，路由给专业 Agent 协作，
> 高风险操作经**人工审批护栏**把关，全过程通过 WebSocket 实时回传。

技术栈：**Python · FastAPI · LangGraph · PostgreSQL · Redis Pub/Sub · Docker · WebSocket**。

---

## 关于本项目

**本人完成的主要工作**：

| 类别 | 内容 |
|------|------|
| Agent 拓扑重构 | 以**运营主管 Supervisor** 为唯一入口，替换通用节点；新增 5 个领域 Agent（选品 / 订单物流 / 多语言客服 / 定价 / 数据周报）+ 公共基座 `_base.py`；重写 `planner.py` 为**依赖感知 DAG 规划器**、`critic.py` 为运营结果校验 |
| 编排逻辑 | `supervisor.py` 实现目标分解 → 依赖调度 → 条件路由 → 结果汇总 → 异常处理（重试/降级/转人工）；`build_graph.py` 重构节点与条件边并挂载 checkpointer |
| 审批护栏（HITL） | 新建 `graph/approval.py`：审批矩阵（阈值分档）、`interrupt()` 护栏与**不支持 interrupt 时的降级方案**（落库 pending + 白名单 + resume 重放）、审批记录落库 |
| 领域工具集 | 新建 `tools/domain/`：13 个工具 + Pydantic IO Schema + 注册表（含审批标记），默认 Mock 数据离线可跑，预留真实 API 适配器与开关 |
| 数据与事件 | 新建集中建表脚本 `services/schema.sql`（含 9 张运营领域表）、`bus/events.py` 事件规范（`task.* / agent.* / approval.*`）、`services/orchestrator.py` 任务编排器 |
| API | 新建 `/tasks`、`/approvals`、`/agents` 路由与审批恢复接口，改造 WebSocket 支持任务/审批事件流 |
| 示例与测试 | 编写 `sample_data/*.json` + `seed_demo.py` 演示数据；新增 pytest 测试（DAG 编排 / 审批矩阵 / 工具 IO / 任务路由，共 25 例） |
| 文档 | 重写 README，新增架构说明与审批流程文档，并绘制全套流程图（`docs/DIAGRAMS.pdf`） |
| 缺陷修复 | 修复原项目的连接池 `ssl="require"` 硬编码、Windows 绝对路径硬编码、强制 `rediss://`、缺失 `.env.example`、Supabase 客户端导入即崩溃等问题 |

> 说明：原项目自带的通用 Agent（`researcher` / `coder` / `github_agent`）与通用工具（代码执行、OCR、RAG 等）
> 与本项目业务无关，**未接入运营编排**，作为可复用能力保留；原项目的前端控制台未纳入本次改造，
> 已移除（本仓库聚焦后端服务与文档，接口与 WebSocket 事件均已预留）。

---

## 一、核心能力

| 能力 | 说明 |
|------|------|
| 多 Agent 编排 | 运营主管按 DAG 调度，依赖满足才执行后续任务 |
| 领域 Agent | 选品分析 / 订单与物流 / 多语言客服 / 定价 / 数据周报 |
| 人工审批护栏（HITL） | 改价、退款、批量补发、改库存等高风险操作按金额/幅度分档审批 |
| 可解释工具集 | 13 个领域工具，统一 Pydantic IO Schema，Mock/真实 API 可切换 |
| 状态持久化 | 任务、DAG 进度、Agent 执行记录、审批记录全部落库，支持恢复 |
| 实时事件 | Redis Pub/Sub → WebSocket 推送 `task.* / agent.* / approval.*` 事件 |

---

## 二、架构

```
运营目标（自然语言）
        │
        ▼
┌──────────────────────────────┐
│     运营主管 Agent (Supervisor) │  目标→依赖 DAG → 路由 → 汇总 → 异常处理
└───────────────┬──────────────┘
                │  依赖感知 DAG
   ┌────────────┬─────────────┬──────────────┬──────────┬──────────────┐
   ▼            ▼             ▼              ▼          ▼
选品分析     订单与物流     多语言客服       定价       数据周报
selection   order_logistics customer_service pricing  weekly_report
   │            │             │              │          │
   └────────────┴──── 领域工具集 tools/domain ─┴──────────┘
                │
      ┌─────────┴──────────┐
      ▼                    ▼
 审批护栏(HITL)      Redis Pub/Sub 事件
 interrupt/矩阵      → WebSocket 前端
      │                    │
      └────────┬───────────┘
               ▼
      PostgreSQL 持久化（任务/DAG/执行/审批/业务表）
```

详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 与 [`docs/APPROVAL_FLOW.md`](docs/APPROVAL_FLOW.md)。

---

## 三、Agent 角色与职责

| Agent | 职责边界 | 主要工具 | 决策依据 | 失败降级 |
|-------|----------|----------|----------|----------|
| **运营主管 Supervisor** | 目标分解为 DAG、路由、依赖管理、结果汇总、异常处理 | — | DAG 依赖与优先级 | 重复失败/死锁→终止；待审批→挂起 |
| **选品分析 selection** | 市场调研、竞品分析、利润测算、选品建议（不做订单/退款/改价） | market_data、competitor_price、fx_rate、profit_calculator | 需求指数与增长、竞品价格区间、单位毛利 | 市场数据缺失→降置信度并标注；竞品为空→需求驱动建议 |
| **订单与物流 order_logistics** | 订单查询、物流异常分类、补发/退款决策与执行（含审批） | order_query、logistics_track、inventory_query、refund*、inventory_update* | 丢件→有库存补发否则退款；超时→催促+补偿；拒收→退款 | 查询失败→转人工；无库存→退款；超阈值→审批 |
| **多语言客服 customer_service** | 工单分类、情感分析、多语言回复草稿 | ticket_list、ticket_classify、translation、db_query | 关键词分类 + 情感极性 + 知识库命中 | 翻译失败→保留原文；工单失败→跳过记录 |
| **定价 pricing** | 依库存/销量/竞品动态调价（经审批执行） | inventory_query、competitor_price、db_query、pricing_engine、inventory_update* | 库存水位 + 竞品偏离 + 近7天销量 + 保底价 | 竞品缺失→保底价；幅度超限→封顶并审批 |
| **数据周报 weekly_report** | 汇总运营数据、生成周报、推送负责人 | db_query、notify | GMV/销量、异常数、工单分布、调价次数 | 数据缺口→标注；推送失败→落库待重发 |
| **结果校验 critic** | 校验结果可用性与审批闭环 | — | 硬失败标记 + LLM 判定 | 全失败→拒绝；部分失败→LLM 判定 |

> 带 `*` 的工具为**高风险操作**，调用前必经过审批护栏。

---

## 四、审批矩阵（HITL）

| 操作 | 自动执行 | 主管审批 | 经理审批 |
|------|----------|----------|----------|
| 退款 | ≤ $100 | $100 ~ $500 | > $500 |
| 补发 | ≤ $200 | > $200 或批量 ≥ 5 件 | — |
| 改价 | ≤ 5% | 5% ~ 15% | > 15% |
| 改库存 / 上下架 | — | 一律主管审批 | — |

阈值可在 `.env` 中调整（`APPROVAL_*`）。审批流程：**Agent 生成操作建议 → 触发 `interrupt()` 暂停 → 推送 `approval.requested` → 人工审批 → 恢复或取消 → 记录落库**。

---

## 五、工具集（13 个）

| 工具 | 说明 | 需审批 |
|------|------|:------:|
| `market_data` | 市场数据查询（需求/增长/子类目） | |
| `competitor_price` | 竞品价格抓取 | |
| `fx_rate` | 汇率查询 | |
| `profit_calculator` | 利润测算（本地纯函数） | |
| `order_query` | 订单查询 | |
| `logistics_track` | 物流轨迹与异常识别 | |
| `inventory_query` | 库存查询 | |
| `inventory_update` | 库存调整 | ✅ |
| `refund` | 退款执行 | ✅ |
| `ticket_list` | 工单拉取 | |
| `ticket_classify` | 工单分类 + 情感分析 | |
| `translation` | 多语言翻译 | |
| `pricing_engine` | 定价规则引擎 | |
| `db_query` | 受控只读数据库查询 | |
| `notify` | 邮件 / Webhook / WS 推送 | |

所有工具默认走**本地模拟数据**（`MOCK_ENABLED=true`，离线可跑），每个工具均预留真实 API 适配器；把 `MOCK_ENABLED=false` 并填入对应 Key 即可切换真实数据源。

---

## 六、API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/tasks` | 提交运营任务（goal / task_type / params） |
| GET | `/tasks/{task_id}` | 任务状态、DAG、结果 |
| GET | `/tasks/{task_id}/agents` | 各 Agent 执行详情 |
| POST | `/tasks/{task_id}/resume` | 审批后恢复执行 |
| GET | `/approvals` | 审批列表（`?status=pending`） |
| POST | `/approvals/{id}/approve` | 审批通过（记录 + 事件 + 恢复） |
| POST | `/approvals/{id}/reject` | 审批拒绝 |
| GET | `/agents` | 列出所有 Agent 与工具能力矩阵 |
| POST | `/run` | 兼容旧入口，等价于提交运营任务 |
| WS | `/ws/{run_id}` | 实时推送任务进度与审批请求 |

保留原有 `auth / apikeys / admin / plan / tools / uploads / updates` 等路由。

### Redis 事件

`task.created` · `task.decomposed` · `task.completed` · `task.failed` · `agent.started` · `agent.completed` · `agent.failed` · `approval.requested` · `approval.granted` · `approval.rejected` · `tool.called`

频道：`task:{task_id}`（运营任务）、`run:{run_id}`（兼容旧入口）。

---

## 七、数据库

集中建表脚本：`backend/services/schema.sql`（启动时由 `services/db.py::init_db()` 幂等执行）。

- 账号/会话/偏好/API Key：`users` `sessions` `user_preferences` `user_api_keys`
- 运行与日志：`agent_runs` `activity_logs` `cached_runs` `assistant_memory` `uploaded_files`
- 运营领域：`operations_tasks` `agent_executions` `approvals` `order_exceptions`
  `shipments` `pricing_history` `support_tickets` `product_candidates` `notifications`
- 演示数据表：`inventory` `competitor_prices` `sales_daily`

---

## 八、快速开始

```bash
# 1. 准备环境变量
cp .env.example .env        # 填入 GROQ_API_KEY / JWT_SECRET 等

# 2. 启动 Postgres + Redis
docker compose up -d

# 3. 后端
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python seed_demo.py                                  # 灌入演示数据（可选）
uvicorn main:app --reload                            # http://localhost:8000

# 4. 打开交互式 API 文档验证服务
#    http://localhost:8000/docs
```

> 服务启动时会自动幂等执行建表脚本，无需手动初始化数据库。
> 本地 `docker-compose` 的 Postgres 不支持 TLS，请保持 `DB_SSL_MODE=disable`；云库（Neon/Render）改为 `require`。

---

## 九、示例任务

```bash
# 1) 选品分析
curl -X POST localhost:8000/tasks -b cookie.txt -H 'Content-Type: application/json' -d '{
  "goal": "分析手机配件在东南亚市场的选品机会，输出选品建议和竞品分析",
  "params": {"market": "SEA", "category": "phone_accessories", "sku": "SKU-PHONE-CASE-01"}
}'

# 2) 物流异常处理（会触发退款审批）
curl -X POST localhost:8000/tasks -b cookie.txt -H 'Content-Type: application/json' -d '{
  "goal": "处理本周物流异常，协调补发或退款",
  "params": {"limit": 10}
}'

# 3) 定价调整（改价幅度超阈值会进入审批）
curl -X POST localhost:8000/tasks -b cookie.txt -H 'Content-Type: application/json' -d '{
  "goal": "根据库存和销售数据动态调整商品定价",
  "params": {"market": "SEA"}
}'

# 4) 客服工单分类与回复草稿
curl -X POST localhost:8000/tasks -b cookie.txt -H 'Content-Type: application/json' -d '{
  "goal": "监控多语言客服工单，自动分类并生成回复草稿",
  "params": {"status": "open"}
}'

# 5) 数据周报
curl -X POST localhost:8000/tasks -b cookie.txt -H 'Content-Type: application/json' -d '{
  "goal": "汇总本周运营数据，生成周报并推送给运营负责人"
}'

# 查看审批并处理
curl localhost:8000/approvals?status=pending -b cookie.txt
curl -X POST localhost:8000/approvals/<id>/approve -b cookie.txt -d '{"decided_by":"ops_lead"}'
```

---

## 十、测试

```bash
cd backend
pytest -q
```

覆盖：Supervisor DAG 编排（`test_supervisor_dag.py`）、审批护栏与矩阵（`test_approval_guardrail.py`）、
领域工具 IO 与 mock 路径（`test_domain_tools.py`）、任务/Agent 路由（`test_api_tasks.py`）。

---

## 十一、目录结构（关键部分）

```
backend/
├── config.py                 ← 全局配置（含审批阈值 / MOCK / HITL 开关）
├── main.py                   ← FastAPI 入口 + WebSocket
├── seed_demo.py              ← 演示数据灌库
├── graph/
│   ├── state.py              ← 共享状态（DAG/审批/执行轨迹）
│   ├── supervisor.py         ← 运营主管 Agent
│   ├── approval.py           ← 人工审批护栏（矩阵 + interrupt + 落库）
│   ├── build_graph.py        ← LangGraph 编排图（+checkpointer）
│   ├── router.py             ← 领域路由 / slash 命令
│   └── agents/
│       ├── planner.py            ← 运营 DAG 规划器
│       ├── selection_agent.py    ← 选品分析
│       ├── order_logistics_agent.py ← 订单与物流
│       ├── customer_service_agent.py ← 多语言客服
│       ├── pricing_agent.py      ← 定价
│       ├── weekly_report_agent.py← 数据周报
│       └── critic.py             ← 结果校验
├── tools/domain/             ← 13 个领域工具 + schemas + registry
├── services/
│   ├── db.py / schema.sql    ← 连接池 + 集中建表
│   └── orchestrator.py       ← 任务编排器（驱动图 + 落库 + 事件）
├── routes/tasks.py           ← /tasks
├── routes/approvals.py       ← /approvals
├── routes/agents.py          ← /agents
├── bus/events.py             ← Redis 事件规范
├── sample_data/              ← 演示数据（订单/库存/工单/竞品价/选品）
└── tests/                    ← pytest 用例

docs/
├── ARCHITECTURE.md           ← 架构说明（分层架构 / 协作时序）
├── APPROVAL_FLOW.md          ← 审批流程与审批矩阵
├── DIAGRAMS.pdf              ← 全部流程图（矢量 PDF）
└── REFACTOR_PLAN.md          ← 改造计划与范围
```

---

## 十二、说明

- **纯 Python 后端**，未引入 Java/Go/C++；Docker 执行器保留但不承载业务逻辑。
- Agent 侧：`planner` 为运营 DAG 规划器，`critic` 为运营结果校验，5 个领域 Agent 各自有明确职责边界；
  通用能力（`researcher` / `coder` / `github_agent`）未接入运营编排，保留文件可复用。
- 代码与文档中的品牌、订单、用户、竞品数据均为虚构，不含任何真实个人信息或真实密钥。
