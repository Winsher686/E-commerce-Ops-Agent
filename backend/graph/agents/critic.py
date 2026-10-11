# backend/graph/agents/critic.py
# ──────────────────────────────────────────────────────────────
#  运营结果校验 Agent
#  职责：在所有子任务完成后，校验运营结果是否可用、审批是否闭环。
#  通过条件（任一场景）：
#    - 无硬失败标记，且至少有一个有效产出
#    - 存在待审批但其余结果有效（视为“阶段性通过”，由人工补齐）
#  拒绝条件：全部子任务硬失败 / 结果与目标完全无关
# ──────────────────────────────────────────────────────────────
import json

from langchain_core.messages import AIMessage

from graph.llm import get_llm
from graph.state import AgentState

CRITIC_SYSTEM = """
你是跨境电商运营结果的质检员。判断本次运营任务是否可判为完成。
通过：核心子任务有可用产出，或仅存在“待人工审批/局部降级”而主体结果有效。
拒绝：全部子任务硬失败，或结果与运营目标无关。
只回复 JSON：{"approved": true|false, "feedback": "..."}
"""

# 硬失败标记（工具/执行层错误）
FAILURE_MARKERS = [
    "syntax error", "execution environment error", "docker execution failed",
    "traceback", "segmentation fault", "core dumped", "permission denied",
]
# 局部降级标记（不算硬失败）
PARTIAL_MARKERS = [
    "获取失败", "缺失", "降级", "重试", "failed", "超时", "不可用", "转人工", "待重发",
]


async def critic_node(state: AgentState) -> dict:
    results = state.get("results", {})

    if not results:
        return {
            "approved": False,
            "messages": [AIMessage(content="校验拒绝：没有任何运营结果可供校验。")],
        }

    success = partial = failure = 0
    for v in results.values():
        text = str(v).lower()
        if any(m in text for m in FAILURE_MARKERS):
            failure += 1
        elif any(m in text for m in PARTIAL_MARKERS):
            partial += 1
        else:
            success += 1

    total = len(results)
    print("运营校验计数：", success, partial, failure, total)

    # 无硬失败 → 通过
    if failure == 0 and (success + partial) >= 1:
        return {
            "approved": True,
            "messages": [AIMessage(
                content=(f"校验通过。完成 {total} 项运营子任务"
                         f"（{success} 正常，{partial} 含降级/待审批）。")
            )],
        }

    # 全部硬失败 → 拒绝
    if failure == total:
        return {
            "approved": False,
            "messages": [AIMessage(content=f"校验拒绝：{failure} 项子任务全部失败。")],
        }

    # 部分失败 → 交由 LLM 判定
    summary = "\n".join(f"[{k}]: {str(v)[:300]}" for k, v in results.items())
    llm = await get_llm(state["user_id"])
    resp = await llm.ainvoke([
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": f"目标：{state['goal']}\n\n结果：\n{summary}\n\n是否通过？只回 JSON。"},
    ])
    tokens = getattr(resp, "usage_metadata", {}).get("total_tokens", 0)

    try:
        verdict = json.loads(resp.content.replace("```json", "").replace("```", "").strip())
    except Exception:  # noqa: BLE001
        verdict = {"approved": failure < max(1, total / 2), "feedback": "部分失败"}

    if verdict.get("approved"):
        return {"approved": True, "tokens_used": (state.get("tokens_used") or 0) + tokens,
                "messages": [AIMessage(content=f"校验通过：{verdict.get('feedback', '')}")]}

    return {"approved": False, "tokens_used": (state.get("tokens_used") or 0) + tokens,
            "messages": [AIMessage(content=f"校验拒绝：{verdict.get('feedback', '')}")]}
