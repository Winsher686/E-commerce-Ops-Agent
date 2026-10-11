# backend/tools/domain/refund_api.py
# ──────────────────────────────────────────────────────────────
#  工具：退款接口（高风险 —— 必须经审批护栏）
#  用途：订单与物流 Agent 处理退款。
#  权限：调用前系统已按审批矩阵判定；本工具仅负责执行。
#  降级：执行失败返回 success=False + message，由 Agent 转人工。
# ──────────────────────────────────────────────────────────────
import uuid

from config import get_settings
from tools.domain._common import http_get
from tools.domain.schemas import RefundInput, RefundOutput

settings = get_settings()


async def run(inp: RefundInput) -> RefundOutput:
    if settings.mock_enabled:
        return RefundOutput(
            task_id=inp.task_id,
            success=True,
            refund_id=f"RF-{uuid.uuid4().hex[:10].upper()}",
            order_id=inp.order_id,
            amount_usd=inp.amount_usd,
            status="refunded",
            message=f"mock 退款 {inp.amount_usd} USD（{inp.method}）",
        )

    data = await http_get(
        f"{settings.refund_api_base}/v1/refund",
        params={
            "order_id": inp.order_id,
            "amount": inp.amount_usd,
            "reason": inp.reason,
            "method": inp.method,
        },
        headers={"Authorization": f"Bearer {settings.refund_api_key}"},
    )
    return RefundOutput(
        task_id=inp.task_id,
        success=bool(data.get("success", False)),
        refund_id=data.get("refund_id", ""),
        order_id=inp.order_id,
        amount_usd=inp.amount_usd,
        status=data.get("status", ""),
        message=data.get("message", ""),
    )
