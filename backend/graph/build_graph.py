# backend/graph/build_graph.py
# ──────────────────────────────────────────────────────────────
#  跨境电商运营中台 —— LangGraph 编排图
#
#  拓扑：supervisor 为唯一入口，按 DAG 调度领域 Agent，全部完成后由 critic 校验。
#  节点：supervisor / planner / selection / order_logistics /
#        customer_service / pricing / weekly_report / critic
#
#  关于 checkpointer：HITL 审批护栏的 interrupt() 需要图挂载 checkpointer。
#  这里优先使用内存 checkpointer（MemorySaver）；若当前 langgraph 版本不支持，
#  则退化为无 checkpointer（审批护栏自动降级为“落库 pending + 结束本轮”）。
# ──────────────────────────────────────────────────────────────
from langgraph.graph import END, StateGraph

from graph.agents.critic import critic_node
from graph.agents.customer_service_agent import customer_service_node
from graph.agents.order_logistics_agent import order_logistics_node
from graph.agents.planner import planner_node
from graph.agents.pricing_agent import pricing_node
from graph.agents.selection_agent import selection_node
from graph.agents.weekly_report_agent import weekly_report_node
from graph.state import AgentState
from graph.supervisor import DOMAIN_AGENTS, supervisor_node

# ── checkpointer（可选）────────────────────────────────────────
def _make_checkpointer():
    try:
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore
        return MemorySaver()
    except Exception:  # noqa: BLE001
        try:
            from langgraph.checkpoint.memory import InMemorySaver  # type: ignore
            return InMemorySaver()
        except Exception:  # noqa: BLE001
            return None


NODE_FUNCS = {
    "planner": planner_node,
    "selection": selection_node,
    "order_logistics": order_logistics_node,
    "customer_service": customer_service_node,
    "pricing": pricing_node,
    "weekly_report": weekly_report_node,
    "critic": critic_node,
}


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("supervisor", supervisor_node)
    for name, fn in NODE_FUNCS.items():
        g.add_node(name, fn)

    g.set_entry_point("supervisor")

    # supervisor 条件路由：next 字段决定去向
    routes = {name: name for name in NODE_FUNCS}
    routes["END"] = END
    g.add_conditional_edges(
        "supervisor",
        lambda state: state.get("next", "END"),
        routes,
    )

    # 各节点执行完回到 supervisor，形成「调度-执行」循环
    for name in NODE_FUNCS:
        g.add_edge(name, "supervisor")

    checkpointer = _make_checkpointer()
    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()


# 全局编译一次
GRAPH = build_graph()

__all__ = ["GRAPH", "build_graph", "DOMAIN_AGENTS"]
