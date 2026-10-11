# backend/tools/domain/ticket_api.py
# ──────────────────────────────────────────────────────────────
#  工具：工单系统接口
#  用途：多语言客服 Agent 拉取工单、按规则分类并输出建议动作。
#  数据源：mock 读 sample_data/tickets.json；真实模式调 TICKET_API_BASE。
#  降级：拉取失败返回空列表；分类走本地关键词规则，不依赖外部服务。
# ──────────────────────────────────────────────────────────────
from config import get_settings
from tools.domain._common import http_get, load_sample
from tools.domain.schemas import (
    SupportTicket,
    TicketClassifyInput,
    TicketClassifyOutput,
    TicketListInput,
    TicketListOutput,
)

settings = get_settings()

# 关键词 → 分类 / 情感（本地可解释规则，供分类工具与降级使用）
_CATEGORY_RULES = {
    "物流": ["物流", "快递", "shipping", "delivery", "tracking", "包裹", "未收到"],
    "退换货": ["退款", "退货", "refund", "return", "exchange"],
    "产品咨询": ["怎么用", "how to", "size", "尺寸", "material", "材质", "spec"],
    "投诉": ["投诉", "差评", "complaint", "bad", "terrible", "垃圾"],
}
_NEGATIVE = ["投诉", "差评", "refund", "complaint", "terrible", "bad", "垃圾", "慢", "失望"]
_POSITIVE = ["谢谢", "thank", "great", "好", "满意", "love"]


def classify_text(message: str) -> tuple[str, str, float]:
    text = (message or "").lower()
    category = "其他"
    for cat, kws in _CATEGORY_RULES.items():
        if any(k.lower() in text for k in kws):
            category = cat
            break
    if any(k.lower() in text for k in _NEGATIVE):
        sentiment = "negative"
    elif any(k.lower() in text for k in _POSITIVE):
        sentiment = "positive"
    else:
        sentiment = "neutral"
    confidence = 0.75 if category != "其他" else 0.4
    return category, sentiment, confidence


async def list_tickets(inp: TicketListInput) -> TicketListOutput:
    if settings.mock_enabled:
        records = load_sample("tickets") or []
        rows = records
        if inp.status:
            rows = [r for r in rows if r.get("status") == inp.status]
        if inp.language:
            rows = [r for r in rows if r.get("language") == inp.language]
        if inp.category:
            rows = [r for r in rows if r.get("category") == inp.category]
        tickets = [
            SupportTicket(**{k: r.get(k) for k in SupportTicket.model_fields})
            for r in rows[: inp.limit]
        ]
        return TicketListOutput(task_id=inp.task_id, tickets=tickets, count=len(tickets), source="mock")

    data = await http_get(
        f"{settings.ticket_api_base}/v1/tickets",
        params={"status": inp.status, "language": inp.language, "limit": inp.limit},
        headers={"Authorization": f"Bearer {settings.ticket_api_key}"},
    )
    tickets = [SupportTicket(**t) for t in data.get("tickets", [])]
    return TicketListOutput(task_id=inp.task_id, tickets=tickets, count=len(tickets), source="api")


async def classify(inp: TicketClassifyInput) -> TicketClassifyOutput:
    category, sentiment, conf = classify_text(inp.message)
    action = {
        "物流": "建议：优先核对物流轨迹并给出预计送达时间",
        "退换货": "建议：核实订单与政策，生成退换货指引草稿",
        "产品咨询": "建议：检索知识库并生成规格说明回复",
        "投诉": "建议：安抚并升级至主管，附补偿方案",
    }.get(category, "建议：人工复核后回复")
    return TicketClassifyOutput(
        task_id=inp.task_id, ticket_no=inp.ticket_no, category=category,
        sentiment=sentiment, confidence=conf, recommended_action=action,
    )
