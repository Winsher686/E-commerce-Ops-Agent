# backend/routes/agents.py
# ──────────────────────────────────────────────────────────────
#  Agent 目录 API
#    GET /agents         列出所有领域 Agent 及其职责/工具/状态
# ──────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends

from routes.dependencies import get_current_user
from tools.domain import registry

router = APIRouter(prefix="/agents", tags=["agents"])

# 领域 Agent 元数据（职责边界 / 工具集 / 决策依据 / 降级策略）
AGENTS = [
    {
        "name": "supervisor", "label": "运营主管 Agent", "kind": "orchestrator",
        "duties": "接收运营目标，分解为依赖 DAG，路由到专业 Agent 并汇总结果",
        "tools": [], "status": "ready",
    },
    {
        "name": "selection", "label": "选品分析 Agent", "kind": "domain",
        "duties": "市场调研、竞品分析、利润测算、选品建议（不处理订单/退款/改价）",
        "tools": ["market_data", "competitor_price", "fx_rate", "profit_calculator"],
        "decision": "需求指数与增长 + 竞品价格区间 + 单位毛利与盈亏平衡价",
        "fallback": "市场数据缺失→降低置信度并标注；竞品为空→改用需求驱动建议",
        "status": "ready",
    },
    {
        "name": "order_logistics", "label": "订单与物流 Agent", "kind": "domain",
        "duties": "订单状态查询、物流异常分类、补发/退款决策与执行（含审批）",
        "tools": ["order_query", "logistics_track", "inventory_query", "refund", "inventory_update"],
        "decision": "丢件→有库存补发否则退款；超时→催促+补偿；拒收→退款；地址错误→转客服",
        "fallback": "物流查询失败→转人工；无库存→退款；超阈值→进入审批",
        "status": "ready",
    },
    {
        "name": "customer_service", "label": "多语言客服 Agent", "kind": "domain",
        "duties": "工单分类、情感分析、多语言回复草稿生成",
        "tools": ["ticket_list", "ticket_classify", "translation", "db_query"],
        "decision": "关键词分类 + 情感极性 + 知识库命中",
        "fallback": "翻译失败→保留原文并标注；工单拉取失败→跳过并记录",
        "status": "ready",
    },
    {
        "name": "pricing", "label": "定价 Agent", "kind": "domain",
        "duties": "依据库存/销量/竞品动态调整定价（经审批后执行）",
        "tools": ["inventory_query", "competitor_price", "db_query", "pricing_engine", "inventory_update"],
        "decision": "库存水位 + 竞品均价偏离 + 近7天销量 + 保底价",
        "fallback": "竞品缺失→按保底价；幅度超限→自动封顶并进入审批",
        "status": "ready",
    },
    {
        "name": "weekly_report", "label": "数据周报 Agent", "kind": "domain",
        "duties": "汇总运营数据、生成周报、推送运营负责人",
        "tools": ["db_query", "notify"],
        "decision": "GMV/销量 + 异常订单数 + 工单量与情感分布 + 本周调价次数",
        "fallback": "数据缺口→报告中标注；推送失败→落库待重发",
        "status": "ready",
    },
    {
        "name": "critic", "label": "结果校验 Agent", "kind": "qa",
        "duties": "校验运营结果可用性与审批闭环",
        "tools": [], "status": "ready",
    },
]


@router.get("")
async def list_agents(user=Depends(get_current_user)) -> dict:
    # 附带工具注册信息，便于前端展示能力矩阵
    tools = [
        {
            "name": t.name, "description": t.description,
            "requires_approval": t.requires_approval,
            "approval_type": t.approval_type or None,
            "tags": t.tags,
        }
        for t in registry.TOOLS.values()
    ]
    return {"agents": AGENTS, "tools": tools, "count": len(AGENTS)}
