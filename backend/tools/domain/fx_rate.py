# backend/tools/domain/fx_rate.py
# ──────────────────────────────────────────────────────────────
#  工具：汇率查询
#  用途：利润测算 / 定价时在 USD 与本地币种间换算。
#  降级：真实接口失败时回退到配置中的 USD/CNY 参考汇率并标注 source=mock。
# ──────────────────────────────────────────────────────────────
from config import get_settings
from tools.domain._common import http_get
from tools.domain.schemas import FXRateInput, FXRateOutput

settings = get_settings()

# 常见货币对参考汇率（mock / 降级用）
_REFERENCE = {
    ("USD", "CNY"): 7.20,
    ("USD", "THB"): 36.50,
    ("USD", "IDR"): 15800.0,
    ("USD", "VND"): 25400.0,
    ("USD", "MYR"): 4.70,
    ("USD", "PHP"): 57.00,
    ("USD", "SGD"): 1.34,
}


async def run(inp: FXRateInput) -> FXRateOutput:
    base = inp.base.upper()
    quote = inp.quote.upper()

    if settings.mock_enabled:
        rate = _REFERENCE.get((base, quote))
        if rate is None:
            rate = settings.default_fx_usd_cny if (base, quote) == ("USD", "CNY") else 1.0
        return FXRateOutput(task_id=inp.task_id, base=base, quote=quote, rate=rate,
                            as_of="mock", source="mock")

    # ── 真实 API 适配器 ───────────────────────────────────────
    try:
        data = await http_get(
            "https://api.fx.example.com/v1/rate",
            params={"base": base, "quote": quote},
            headers={"Authorization": f"Bearer {settings.fx_api_key}"},
        )
        return FXRateOutput(task_id=inp.task_id, base=base, quote=quote,
                            rate=float(data["rate"]), as_of=data.get("as_of", ""), source="api")
    except Exception:
        # 降级：使用参考汇率
        rate = _REFERENCE.get((base, quote), settings.default_fx_usd_cny)
        return FXRateOutput(task_id=inp.task_id, base=base, quote=quote, rate=rate,
                            as_of="fallback", source="fallback")
