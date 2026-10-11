# backend/tools/domain/notify.py
# ──────────────────────────────────────────────────────────────
#  工具：通知 / 推送
#  用途：周报推送运营负责人、审批提醒。
#  渠道：email（SMTP/HTTP 网关）、webhook、websocket（走 Redis 事件）。
#  降级：发送失败返回 success=False，通知落库为 failed 待重发。
# ──────────────────────────────────────────────────────────────
import uuid

from config import get_settings
from tools.domain._common import http_post
from tools.domain.schemas import NotifyInput, NotifyOutput

settings = get_settings()


async def run(inp: NotifyInput) -> NotifyOutput:
    if settings.mock_enabled:
        return NotifyOutput(
            task_id=inp.task_id,
            success=True,
            channel=inp.channel,
            message_id=f"MSG-{uuid.uuid4().hex[:10].upper()}",
            detail=f"mock 已通过 {inp.channel} 发送给 {inp.recipient or '运营负责人'}",
        )

    try:
        if inp.channel == "webhook" and settings.notify_webhook_url:
            data = await http_post(
                settings.notify_webhook_url, json_body={"subject": inp.subject, "body": inp.body}
            )
            return NotifyOutput(
                task_id=inp.task_id,
                success=True,
                channel="webhook",
                message_id=data.get("id", ""),
                detail="webhook sent",
            )

        # 默认走邮件网关（可通过 NOTIFY_WEBHOOK_URL 指向自建网关）
        endpoint = settings.notify_webhook_url or "https://api.notify.example.com/v1/email"
        data = await http_post(
            endpoint,
            json_body={
                "from": settings.notify_email_from,
                "to": inp.recipient,
                "subject": inp.subject,
                "body": inp.body,
            },
        )
        return NotifyOutput(
            task_id=inp.task_id,
            success=True,
            channel=inp.channel,
            message_id=data.get("id", ""),
            detail="email sent",
        )
    except Exception as e:  # noqa: BLE001
        return NotifyOutput(
            task_id=inp.task_id, success=False, channel=inp.channel, detail=f"发送失败：{e}"
        )
