# backend/tools/domain/logistics_api.py
# ──────────────────────────────────────────────────────────────
#  工具：物流查询
#  用途：订单与物流 Agent 追踪物流轨迹并识别异常（丢件/超时/拒收）。
#  数据源：mock 读 sample_data/shipments.json；真实模式调 LOGISTICS_API_BASE。
#  降级：超时重试后仍失败 → 标记 is_exception=True, exception_type=查询失败。
# ──────────────────────────────────────────────────────────────
from config import get_settings
from tools.domain._common import http_get, load_sample
from tools.domain.schemas import (
    LogisticsEvent,
    LogisticsTrackInput,
    LogisticsTrackOutput,
)

settings = get_settings()

# 判定为异常的物流状态
_EXCEPTION_STATUS = {
    "lost": "丢件",
    "delayed": "超时未达",
    "returned": "拒收退回",
    "address_error": "地址错误",
    "customs_hold": "清关滞留",
}


async def run(inp: LogisticsTrackInput) -> LogisticsTrackOutput:
    if settings.mock_enabled:
        records = load_sample("shipments") or []
        row = None
        for r in records:
            if (inp.order_id and r.get("order_id") == inp.order_id) or (
                inp.tracking_no and r.get("tracking_no") == inp.tracking_no
            ):
                row = r
                break

        if not row:
            return LogisticsTrackOutput(
                task_id=inp.task_id,
                order_id=inp.order_id or "",
                tracking_no=inp.tracking_no or "",
                status="unknown",
                is_exception=True,
                exception_type="查询不到物流记录",
                source="mock(empty)",
            )

        status = row.get("status", "")
        exc_type = _EXCEPTION_STATUS.get(status, "")
        events = [LogisticsEvent(**e) for e in row.get("events", [])]
        return LogisticsTrackOutput(
            task_id=inp.task_id,
            order_id=row.get("order_id", ""),
            tracking_no=row.get("tracking_no", ""),
            carrier=row.get("carrier", ""),
            status=status,
            events=events,
            is_exception=bool(exc_type),
            exception_type=exc_type,
            source="mock",
        )

    # ── 真实 API 适配器 ───────────────────────────────────────
    data = await http_get(
        f"{settings.logistics_api_base}/v1/track",
        params={"order_id": inp.order_id, "tracking_no": inp.tracking_no},
        headers={"Authorization": f"Bearer {settings.logistics_api_key}"},
    )
    events = [LogisticsEvent(**e) for e in data.get("events", [])]
    return LogisticsTrackOutput(
        task_id=inp.task_id,
        order_id=data.get("order_id", inp.order_id or ""),
        tracking_no=data.get("tracking_no", inp.tracking_no or ""),
        carrier=data.get("carrier", ""),
        status=data.get("status", ""),
        events=events,
        is_exception=bool(data.get("is_exception", False)),
        exception_type=data.get("exception_type", ""),
        source="api",
    )
