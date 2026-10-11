# 需求与流程设计 —— 电商运营中台

> 本文把「电商运营中台」从一句定位，落成 **业务需求 → 端到端流程** 的完整设计。
> 每一节都与代码实现一一对应（Agent / 工具 / 审批 / 数据表 / 事件），
> 回答两个问题：**业务到底要什么**、**系统具体怎么跑**。

---

## 一、业务背景与目标

电商运营的日常，本质是 **看数据 → 做决策 → 执行动作** 的循环。三个痛点：

1. **环节分散**：选品、订单物流、客服、定价、报表各自一套后台，人要在多个系统间反复切换。
2. **高风险动作损失直接**：退款、改价、改库存、批量补发一旦做错，钱和口碑都受损。
3. **跨环节有依赖**：必须先查清异常，才能决定补发还是退款；人工串流程容易漏、容易错。

**目标**：运营人员用**一句话**下达目标，系统自动拆成**依赖感知的任务 DAG**，交给专业 Agent 协作，
**高风险动作在落地前必须经过人工审批**，全过程可观测、可追溯、可恢复。

| 量化目标 | 目标值 |
|---|---|
| 人工介入点 | 仅在高风险操作审批处，其余全自动 |
| 可追溯性 | 每个子任务 / 每次工具调用 / 每次审批全部落库 |
| 高风险误操作 | 0（未审批不放行执行） |
| 中断可恢复 | 审批挂起后可经接口恢复，不丢 DAG 进度 |

---

## 二、角色与权限

| 角色 | 职责 | 关键权限 |
|---|---|---|
| 运营专员 | 提交运营目标、查看进度与结果 | `POST /tasks` |
| 运营主管（人工） | 审批中等风险操作 | 审批 `supervisor` 档 |
| 经理（人工） | 审批高风险操作 | 审批 `manager` 档 |
| 运营主管 Agent | 目标拆解、DAG 调度、结果汇总、异常处理 | 图编排（唯一入口） |
| 领域 Agent ×5 | 各领域的执行体 | 调用领域工具 |
| 系统（auto） | 阈值内操作自动放行并留痕 | 审批 `auto` 档 |

---

## 三、业务需求清单

> 优先级：**P0 = 核心闭环（面试主推）**，P1 = 支撑能力，P2 = 增强项。

### 跨场景需求

| ID | 需求 | 优先级 | 涉及 | 验收标准 |
|---|---|---|---|---|
| BR-00 | 一句话运营目标 → 自动拆成依赖 DAG | P0 | supervisor + planner | 输入目标后产出含 `assigned_to / depends_on / priority` 的 DAG |
| BR-01 | 高风险操作落地前必须人工审批 | P0 | approval.gate | 未审批的高风险操作不落库、不执行 |
| BR-02 | 全流程可追溯 | P0 | 各表 | 子任务、工具调用、审批决策均可按 `task_id` 回溯 |
| BR-03 | 审批挂起后可恢复 | P1 | `/tasks/{id}/resume` | 审批通过后重放，DAG 进度不丢 |

### 场景 A：选品分析

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| BR-A1 | 市场调研 + 竞品分析 + 利润测算 → 选品建议 | P1 | 输出建议品类/价格带 + 单位毛利 |
| BR-A2 | 建议按机会大小排序并给出理由 | P1 | 每条建议附决策依据 |

### 场景 B：订单与物流异常处理

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| BR-B1 | 批量识别物流异常订单 | P0 | 产出异常订单清单 + 原因 |
| BR-B2 | 异常原因分类（丢件/超时/拒收/破损） | P0 | 每单有明确原因标签 |
| BR-B3 | 依据原因决策补发或退款 | P0 | 每单有处置结论 |
| BR-B4 | 退款/补发超阈值走审批 | P0 | 审批记录齐全 |
| BR-B5 | 处置结果同步客服（通知草稿） | P1 | 受影响订单有草稿 |

### 场景 C：客服工单

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| BR-C1 | 拉取待处理工单 | P1 | 按状态过滤 |
| BR-C2 | 工单分类 + 情感分析 | P1 | 每单有类别与情感极性 |
| BR-C3 | 生成回复草稿（人审后发送） | P1 | 每单有草稿 |
| BR-C4 | 多语言场景支持 | P2 | 非中文工单可翻译后处理 |

### 场景 D：动态定价

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| BR-D1 | 依库存/销量/竞品算建议售价 | P0 | 每个 SKU 有建议价 + 幅度 |
| BR-D2 | 改价幅度分档审批 | P0 | 审批记录齐全 |
| BR-D3 | 执行改价并记录历史 | P0 | `pricing_history` 有记录 |

### 场景 E：数据周报

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| BR-E1 | 汇总本周运营数据（GMV/销量/异常/工单/调价） | P1 | 指标齐全 |
| BR-E2 | 生成中文周报并推送负责人 | P1 | 推送记录落库 |

---

## 四、端到端流程设计

### 4.1 通用编排流程（主管 Agent 主干）

**业务视图（7 步）**

1. 运营专员提交**一句话目标**
2. 运营主管 Agent 把目标拆成**依赖感知 DAG**
3. 按 `depends_on` + `priority` 调度**可运行**的子任务
4. 领域 Agent 执行并调用领域工具
5. 命中高风险操作 → 进入**审批护栏**（阈值内自动放行；否则暂停等审批）
6. 所有子任务完成 → **结果校验**（critic）
7. **汇总结果** → 推送 / 落库

**系统视图（模块与落点对照）**

| 步骤 | 模块 / 接口 | 落库 | 事件 |
|---|---|---|---|
| 提交 | `POST /tasks` → `services/orchestrator.py` | `operations_tasks` | `task.created` |
| 拆解 | `graph/supervisor.py` → `graph/agents/planner.py` | `operations_tasks.dag` | `task.decomposed` |
| 调度 | supervisor 按依赖与优先级选可运行子任务 | — | `agent.started` |
| 执行 | `graph/agents/*` → `tools/domain/registry.call` | `agent_executions` | `tool.called` / `agent.completed` |
| 审批 | `graph/approval.py::gate`（矩阵 + interrupt / 降级） | `approvals` | `approval.requested` / `granted` / `rejected` |
| 校验 | `graph/agents/critic.py` | `agent_executions` | — |
| 汇总 | `orchestrator` | `operations_tasks.result` | `task.completed` |

> 依赖调度规则（`supervisor_node` 实现）：仅当某子任务的**所有** `depends_on` 都出现在
> `results` 中时才可调度；多任务同层时可并行（受 `AGENT_MAX_CONCURRENCY` 限制）。

---

### 4.2 场景一：物流异常处理（核心闭环 · P0）

**业务目标**：处理本周物流异常订单——能补发的补发、该退款的退款，并通知客服。

**业务流程**

| # | 步骤 | 执行者 | 输入 | 动作 | 输出 | 触发条件 |
|---|---|---|---|---|---|---|
| 1 | 提交目标 | 运营专员 | 时间范围 | 提交"处理本周物流异常，协调补发或退款" | `task_id` | 手动 |
| 2 | 识别异常 | 订单物流 Agent | 订单/时间窗 | `order_query` + `logistics_track` | 异常订单清单 + 原因 | 自动 |
| 3 | 分类原因 | 订单物流 Agent | 异常清单 | 规则 / LLM 分类 | 丢件 / 超时 / 拒收 / 破损 | 自动 |
| 4 | 决策处置 | 订单物流 Agent | 原因 + 库存 | 丢件→有库存则补发否则退款；超时→催促+补偿；拒收→退款 | 处置建议 | 有库存则补发 |
| 5 | **审批门** | 审批护栏 | 金额 / 件数 | 退款 ≤$100 自动、100–500 主管、>500 经理；补发 ≤$200 且 <5 件自动 | 放行 / 挂起 | 命中阈值 |
| 6 | 执行 | 订单物流 Agent | 已通过的建议 | `refund` / `inventory_update` | 执行结果 | 审批通过 |
| 7 | 通知客服 | 客服 Agent | 受影响订单 | 生成客服通知草稿 | 草稿 | 执行完成 |

**依赖 DAG**

```
t1 查询异常并分类(order_logistics, deps=[])
      └─▶ t2 决策补发/退款并执行(order_logistics, deps=[t1])
              └─▶ t3 生成客服通知(customer_service, deps=[t2])
```

**关键设计**
- **工具**：`order_query` `logistics_track` `inventory_query` `refund*` `inventory_update*`（`*` = 需审批）
- **审批**：`refund` / `reship`（阈值见第五章）
- **降级**：查询失败→该单标记 `failed` 并转人工；无库存→走退款；超阈值→审批挂起
- **落库**：`order_exceptions` `shipments` `approvals` `agent_executions`
- **验收**：异常订单 100% 有处置结论；每笔退款/补发都有 `approvals` 记录；受影响订单有客服草稿

---

### 4.3 场景二：动态定价（核心闭环 · P0）

**业务目标**：根据库存水位与近 7 天销量、竞品价格，动态调整商品定价。

**业务流程**

| # | 步骤 | 执行者 | 输入 | 动作 | 输出 |
|---|---|---|---|---|---|
| 1 | 读取数据 | 定价 Agent | SKU 列表 | `inventory_query` + `db_query`(销量) + `competitor_price` | 库存 / 销量 / 竞品价 |
| 2 | 计算建议价 | 定价 Agent | 上述数据 | `pricing_engine`（规则：库存水位 + 竞品偏离 + 保底价） | 建议价 + 改动幅度 |
| 3 | **审批门** | 审批护栏 | 幅度 `change_pct` | ≤5% 自动、5–15% 主管、>15% 经理 | 放行 / 挂起 |
| 4 | 执行改价 | 定价 Agent | 已通过的建议 | 写回价格 + `pricing_history` | 改价结果 |
| 5 | 记录 | 数据周报 Agent（可选） | 改价结果 | 纳入周报统计 | — |

**依赖 DAG**

```
t1 计算各 SKU 建议售价(pricing, deps=[]) ─▶ t2 汇总周报(weekly_report, deps=[t1])
```

**关键设计**
- **工具**：`inventory_query` `competitor_price` `db_query` `pricing_engine` `inventory_update*`
- **降级**：竞品价缺失→退回保底价；幅度超上限→封顶后仍要审批
- **落库**：`pricing_history` `inventory` `competitor_prices` `approvals`
- **验收**：每个 SKU 有建议价与幅度；所有改价经审批；历史可回溯

---

### 4.4 场景三：选品分析（P1）

**流程**：`market_data`（需求/增长/子类目）→ `competitor_price`（竞品价格带）→
`fx_rate`（币种换算）→ `profit_calculator`（单位毛利 / 盈亏平衡价）→ LLM 汇总为**选品建议 + 排序 + 理由**。

- **依赖**：单节点，无高危操作，**无审批**
- **降级**：市场数据缺失→降置信度并显式标注；竞品为空→按需求驱动给建议
- **落库**：`product_candidates`
- **验收**：输出建议品类/价格带 + 单位毛利 + 决策依据

---

### 4.5 场景四：客服工单（P1）

**流程**：`ticket_list`（拉取）→ `ticket_classify`（分类 + 情感）→
`db_query` / 知识库检索（命中答案）→ 生成**回复草稿**；非中文工单先经 `translation`。

- **依赖**：串行流水线
- **审批**：回复草稿**不直接发送**，人审后由客服发出（低风险，不入护栏）
- **降级**：翻译失败→保留原文并标注；工单接口失败→跳过并记录
- **落库**：`support_tickets`
- **验收**：每单有分类、情感、草稿

---

### 4.6 场景五：数据周报（P1）

**流程**：`db_query` 聚合本周指标（GMV / 销量 / 异常数 / 工单分布 / 调价次数）→
LLM 生成**中文周报** → `notify` 推送运营负责人。

- **依赖**：常作为其他场景的**下游汇总节点**
- **降级**：数据缺口→标注；推送失败→落库待重发
- **落库**：`notifications`
- **验收**：指标齐全、推送有记录

---

## 五、跨场景机制

### 5.1 审批矩阵（`config.py` 的 `APPROVAL_*` 可调）

| 操作 | 自动执行（auto） | 主管审批（supervisor） | 经理审批（manager） |
|---|---|---|---|
| 退款 | ≤ $100 | $100 ~ $500 | > $500 |
| 补发 | ≤ $200 且 < 5 件 | > $200 或批量 ≥ 5 件 | — |
| 改价 | ≤ 5% | 5% ~ 15% | > 15% |
| 改库存 / 上下架 | — | 一律主管审批 | — |

**流程**：Agent 生成操作建议 → `gate()` 判定层级 → 阈值内直接执行；
否则 `interrupt()` 暂停（或降级为落库 pending）→ 推送 `approval.requested` → 人工审批 →
写白名单 → `POST /tasks/{id}/resume` 恢复或取消 → 全程写 `approvals`。

### 5.2 依赖调度与并发
- 仅当子任务的全部 `depends_on` 完成后才调度；同层无依赖任务可并行。
- 并发上限 `AGENT_MAX_CONCURRENCY`；连续"无待执行任务"≥3 次触发**死锁终止**。

### 5.3 异常处理与降级
| 情况 | 处理 |
|---|---|
| 工具超时 / 失败 | 统一包装器 `run_tool`：超时 + 重试 + 降级返回 |
| 数据缺失 | 各 Agent 按领域规则降级（见各场景） |
| 子任务连续失败 | critic 判为不可用 → 拒绝或转人工 |
| 需要审批但无法阻塞 | 落库 pending 并结束本轮，等恢复接口 |

### 5.4 状态持久化与恢复
- 任务/DAG/结果：`operations_tasks`；Agent 执行：`agent_executions`；审批：`approvals`。
- LangGraph 挂 checkpointer，`thread_id = task_id`，支持按任务恢复。

### 5.5 实时事件
`task.created` `task.decomposed` `task.completed` `task.failed` `agent.started` `agent.completed`
`agent.failed` `approval.requested` `approval.granted` `approval.rejected` `tool.called`
→ Redis Pub/Sub（频道 `task:{task_id}`）→ WebSocket `/ws/tasks/{task_id}` 实时回传。

---

## 六、需求 → 设计 追溯矩阵

| 需求 | Agent | 工具 | 审批 | 数据表 | 事件 / 接口 |
|---|---|---|---|---|---|
| BR-00 | supervisor + planner | — | — | operations_tasks | `task.decomposed` |
| BR-01 | approval.gate | 高风险工具 | 矩阵 | approvals | `approval.*` |
| BR-02 | orchestrator | — | — | 全部业务表 | `tool.called` |
| BR-03 | supervisor | — | — | operations_tasks | `POST /tasks/{id}/resume` |
| BR-A1/A2 | selection | market_data, competitor_price, fx_rate, profit_calculator | — | product_candidates | GET /tasks/{id}/agents |
| BR-B1~B4 | order_logistics | order_query, logistics_track, inventory_query, refund*, inventory_update* | refund/reship | order_exceptions, shipments, approvals | approval.requested |
| BR-B5 | customer_service | ticket_list, ticket_classify, translation | — | support_tickets | agent.completed |
| BR-C1~C4 | customer_service | ticket_list, ticket_classify, translation, db_query | — | support_tickets | — |
| BR-D1~D3 | pricing | inventory_query, competitor_price, db_query, pricing_engine, inventory_update* | price_change | pricing_history, approvals | approval.granted |
| BR-E1/E2 | weekly_report | db_query, notify | — | notifications | notify 推送 |

---

## 七、非功能需求

| 维度 | 要求 | 实现 |
|---|---|---|
| 幂等 | 建表与初始化可重复执行 | `schema.sql` + `init_db()` |
| 可观测 | 关键步骤有结构化日志与事件 | structlog + `bus/events.py` |
| 安全 | 数据库查询受控只读 | `db_query` 白名单表 |
| 可移植 | 默认离线可跑 | `MOCK_ENABLED=true` 走本地模拟数据 |
| 可配置 | 阈值/并发/开关可调 | `config.py` + `.env` |
| 可恢复 | 审批中断不丢进度 | checkpointer + `resume` |

---

## 八、MVP 范围建议

| 分层 | 内容 | 说明 |
|---|---|---|
| **P0 主推** | 物流异常处理 + 动态定价 | 两个场景同时覆盖 **依赖 DAG + 审批护栏 + 领域工具**，最能体现设计能力 |
| P1 支撑 | 选品分析 / 客服工单 / 数据周报 | 补齐"五大运营场景"，让平台显得完整 |
| P2 增强 | 多语言、批量补发策略、周报自动推送 | 视时间取舍 |

> 建议对外讲解顺序：**一句话目标 → DAG 拆解 → 依赖调度 → 高风险审批 → 汇总交付**，
> 用"物流异常处理"串一遍完整链路，再说明其余场景复用同一套编排。
