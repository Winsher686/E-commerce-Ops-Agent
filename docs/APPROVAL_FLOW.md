# 审批流程说明 —— 人工审批护栏（HITL）

## 1. 审批流程图

> 📐 **图 3 · 审批护栏流程（HITL）**：见 [`DIAGRAMS.pdf`](DIAGRAMS.pdf) 第 3 页（矢量 PDF）。

## 2. 审批矩阵

> 📐 **图 4 · 审批矩阵（阈值与层级）**：见 [`DIAGRAMS.pdf`](DIAGRAMS.pdf) 第 4 页（矢量 PDF）。

## 3. 审批记录字段

| 字段 | 说明 |
|------|------|
| `id` | 审批 ID |
| `task_id` / `subtask_id` | 关联任务与子任务 |
| `agent` | 发起 Agent |
| `operation_type` | refund / reship / price_change / inventory_update / listing_toggle |
| `payload` | 操作明细（订单号 / SKU / 金额 / 幅度） |
| `amount_usd` | 涉及金额 |
| `required_level` | auto / supervisor / manager |
| `status` | pending / approved / rejected / cancelled |
| `decided_by` / `decided_at` | 审批人与时间 |
| `reason` | 审批理由 / 系统判定依据 |

## 4. 接口

```bash
# 待审批列表
GET  /approvals?status=pending

# 通过（记录 + approval.granted 事件 + 写白名单 + 自动恢复执行）
POST /approvals/{approval_id}/approve   {"decided_by": "ops_lead", "reason": "..."}

# 拒绝（记录 + approval.rejected 事件 + 结束任务）
POST /approvals/{approval_id}/reject    {"decided_by": "ops_lead", "reason": "金额过大"}
```

## 5. 设计取舍

- **为什么用 `_preapproved` 白名单？** 兼容不支持 `interrupt` 的 LangGraph 版本，
  让「审批通过 → 恢复执行」在任意版本下都可用；审计仍以 `approvals` 表为准。
- **恢复为何重跑 DAG？** 当前实现为确定性重放：已批准操作凭白名单直接放行，
  未涉及的部分会重新计算。生产环境可结合 checkpointer 的断点恢复进一步优化为「从断点续跑」。
