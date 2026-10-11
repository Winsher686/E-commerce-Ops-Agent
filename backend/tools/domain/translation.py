# backend/tools/domain/translation.py
# ──────────────────────────────────────────────────────────────
#  工具：翻译
#  用途：多语言客服 Agent 生成英文/本地语言回复草稿。
#  数据源：mock 走简单词典占位；真实模式调 TRANSLATION_API_BASE。
#  降级：失败时返回原文并标注 engine=failed，避免阻塞回复。
# ──────────────────────────────────────────────────────────────
from config import get_settings
from tools.domain._common import http_post
from tools.domain.schemas import TranslateInput, TranslateOutput

settings = get_settings()

# mock 常用短语（仅用于演示，不做真实翻译）
_PHRASEBOOK = {
    ("en", "zh"): {"hello": "你好", "refund": "退款", "sorry": "抱歉"},
    ("zh", "en"): {"你好": "hello", "退款": "refund", "抱歉": "sorry"},
}


async def run(inp: TranslateInput) -> TranslateOutput:
    if settings.mock_enabled:
        table = _PHRASEBOOK.get((inp.source_lang, inp.target_lang), {})
        text = inp.text
        for k, v in table.items():
            text = text.replace(k, v)
        return TranslateOutput(
            task_id=inp.task_id,
            text=text,
            source_lang=inp.source_lang,
            target_lang=inp.target_lang,
            engine="mock",
        )

    # ── 真实 API 适配器 ───────────────────────────────────────
    try:
        data = await http_post(
            f"{settings.translation_api_base}/v1/translate",
            json_body={"text": inp.text, "target": inp.target_lang, "source": inp.source_lang},
            headers={"Authorization": f"Bearer {settings.translation_api_key}"},
        )
        return TranslateOutput(
            task_id=inp.task_id,
            text=data.get("text", inp.text),
            source_lang=data.get("source_lang", inp.source_lang),
            target_lang=inp.target_lang,
            engine="api",
        )
    except Exception:
        # 降级：返回原文
        return TranslateOutput(
            task_id=inp.task_id,
            text=inp.text,
            source_lang=inp.source_lang,
            target_lang=inp.target_lang,
            engine="failed",
        )
