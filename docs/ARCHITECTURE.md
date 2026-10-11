# 架构说明 —— 跨境电商运营中台

## 1. 分层架构

> 📐 **图 1 · 系统分层架构**：见 [`DIAGRAMS.pdf`](DIAGRAMS.pdf) 第 1 页（矢量 PDF）。

## 2. Agent 协作流程

> 📐 **图 2 · Agent 协作时序（一次任务的生命周期）**：见 [`DIAGRAMS.pdf`](DIAGRAMS.pdf) 第 2 页（矢量 PDF）。

## 3. 状态与归约器

`AgentState`（`graph/state.py`）关键字段：

| 字段 | 归约器 | 用途 |
|------|--------|------|
| `messages` | `add_messages` | 对话/消息流 |
| `results` | `merge_results` | 子任务结果，按 subtask id 合并 |
| `approvals` / `executed_ops` / `agent_trace` | `merge_list` | 追加式轨迹 |

## 4. HITL 与 checkpointer

- 编译图时优先挂载内存 checkpointer（`MemorySaver`）；`interrupt()` 依赖 checkpointer 才能暂停/恢复。
- 若当前 LangGraph 版本不支持 `interrupt`，审批护栏自动降级为 **「落库 pending + 结束本轮」**：
  审批接口通过 `_preapproved` 白名单驱动 `POST /tasks/{id}/resume` 重跑，效果等价。
- 详见 [`APPROVAL_FLOW.md`](APPROVAL_FLOW.md)。

## 5. 可观测性

- 每个节点执行写入 `agent_executions`（输入/输出/文本/状态）。
- 事件经 `bus/events.py` 发布到 `task:{task_id}`，WebSocket 转发前端。
- `tool.called` 事件记录工具调用，便于统计成功率与降级率。
