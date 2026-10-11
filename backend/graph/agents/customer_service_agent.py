# backend/graph/agents/customer_service_agent.py
# ──────────────────────────────────────────────────────────────
#  多语言客服 Agent
#  职责边界：
#    ✅ 工单分类、情感分析、多语言回复草稿生成
#    ❌ 不执行退款/补发（由订单与物流 Agent 处理）；不改价
#  可用工具：ticket_list / ticket_classify / translation / db_query(知识库)
#  决策依据：工单关键词分类 + 情感极性 + 知识库命中
#  失败降级：翻译失败 → 保留原文并标注；工单拉取失败 → 跳过并记录
# ──────────────────────────────────────────────────────────────
from config import get_settings
from bus import events
from graph.agents._base import (
    build_result,
    call_tool,
    dependency_context,
    emit,
    noop_message,
    pick_pending,
    synthesize,
    to_json,
)
from graph.state import AgentState

settings = get_settings()

CS_SYSTEM = """
你是跨境电商的多语言客服专家。请根据工单分类、情感分析与知识库结果，
为每张工单生成一段得体、专业、可直接发送的回复草稿（默认英文，可按需附中文对照）。
要求：
1) 先共情、再说明、后给方案；
2) 负面情绪工单需致歉并提供补偿/升级路径；
3) 不要承诺未授权的退款或补发，改为「已为您提交处理」；
4) 输出：按工单号分节，每节含 [分类/情感] + 回复草稿。
用中文说明 + 目标语言草稿。
"""

AGENT = "customer_service"


async def customer_service_node(state: AgentState) -> dict:
    task = pick_pending(state, AGENT)
    if not task:
        return noop_message(AGENT, "多语言客服")

    task_id = state.get("task_id")
    params = state.get("params", {}) or {}
    limit = int(params.get("limit", 20))

    await emit(events.emit_agent_started, task_id, agent=AGENT, subtask_id=task.get("id"))

    # ── 1) 拉取工单 ───────────────────────────────────────────
    tickets_out, tickets_err = await call_tool("ticket_list", {
        "status": params.get("status", "open"),
        "language": params.get("language"),
        "limit": limit,
        "task_id": task_id,
    })
    tickets = list(getattr(tickets_out, "tickets", []) or [])

    # ── 2) 逐单分类 + 翻译 ────────────────────────────────────
    analyzed = []
    for t in tickets[:limit]:
        cls, _ = await call_tool("ticket_classify", {
            "ticket_no": t.ticket_no, "message": t.message, "task_id": task_id,
        })
        item = {
            "ticket_no": t.ticket_no,
            "language": t.language,
            "message": t.message,
            "category": getattr(cls, "category", "其他"),
            "sentiment": getattr(cls, "sentiment", "neutral"),
            "action": getattr(cls, "recommended_action", ""),
        }
        # 非英文工单先翻译成英文，便于生成统一草稿
        if t.language and t.language not in ("en", "zh"):
            tr, _ = await call_tool("translation", {
                "text": t.message, "target_lang": "en", "source_lang": t.language,
                "task_id": task_id,
            })
            item["message_en"] = getattr(tr, "text", t.message)
            item["translation_engine"] = getattr(tr, "engine", "unknown")
        analyzed.append(item)

    # ── 3) 生成回复草稿 ───────────────────────────────────────
    user_prompt = f"""
运营子任务：{task.get('task')}

{ dependency_context(state, task) }

工单总数：{len(tickets)}（拉取错误：{tickets_err or '无'}）
工单分析（含翻译）：{to_json(analyzed)}

请为每张工单生成回复草稿。
"""

    text, tokens = await synthesize(CS_SYSTEM, user_prompt, state["user_id"])

    degraded = []
    if tickets_err:
        degraded.append(f"工单拉取失败({tickets_err})，仅处理已获取数据")

    await emit(events.emit_agent_completed, task_id, agent=AGENT,
               subtask_id=task.get("id"), output={"tickets": len(tickets)})

    return build_result(
        state, task, text,
        tokens_used=tokens,
        executed_ops=[{"agent": AGENT, "subtask_id": task.get("id"),
                       "tickets_processed": len(tickets), "degraded": degraded}],
    )
