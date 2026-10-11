# backend/config.py
# ──────────────────────────────────────────────────────────────
#  电商运营中台 —— 全局配置唯一入口
#  所有模块统一通过 get_settings() 读取，禁止直接使用 os.environ
# ──────────────────────────────────────────────────────────────
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    # ── LLM ───────────────────────────────────────────────────
    groq_api_key: str
    jwt_secret: str

    # ── 数据库 ────────────────────────────────────────────────
    database_url: str
    # 本地 docker-compose 的 Postgres 不支持 TLS，云端托管库通常要求 TLS。
    # 通过 DB_SSL_MODE 显式控制：disable(本地 docker) / require(云端)。
    db_ssl_mode: str = "disable"

    # 可选的 postgres docker 变量
    postgres_user: str | None = None
    postgres_password: str | None = None
    postgres_db: str | None = None
    postgres_host: str | None = None
    postgres_port: str | None = None

    # ── Redis ─────────────────────────────────────────────────
    redis_url: str
    redis_host: str | None = None
    redis_port: str | None = None
    # 是否对 Redis 启用 TLS（Upstash 等云服务需要）。留空则按 URL scheme 自动判断。
    redis_use_tls: bool | None = None

    # ── Google OAuth（可选，用于第三方登录）────────────────────
    google_client_id: str | None = None
    google_client_secret: str | None = None
    backend_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"

    # ══════════════════════════════════════════════════════════
    #  电商运营中台 —— 领域配置
    # ══════════════════════════════════════════════════════════

    # 数据源开关：True 时领域工具走本地模拟数据（离线可跑）；
    # False 时走真实第三方 API 适配器（需在下方填 Key）。
    mock_enabled: bool = True

    # HITL 人工审批护栏总开关
    hitl_enabled: bool = True

    # Agent 并发上限（同层可并行执行的子任务数）
    agent_max_concurrency: int = 3

    # ── 审批阈值（美元）──────────────────────────────────────
    # 退款审批矩阵：<= auto 自动执行；auto~supervisor 主管审批；> supervisor 经理审批
    approval_refund_auto_usd: float = 100.0
    approval_refund_supervisor_usd: float = 500.0
    # 补发审批矩阵：<= auto 自动执行；> auto 主管审批
    approval_reship_auto_usd: float = 200.0
    # 改价审批矩阵：|涨跌幅| <= auto% 自动；auto%~supervisor% 主管；> supervisor% 经理
    approval_price_change_auto_pct: float = 5.0
    approval_price_change_supervisor_pct: float = 15.0
    # 批量补发：单次补发件数超过阈值需审批
    approval_bulk_reship_count: int = 5

    # ── 领域外部系统（真实适配器用，mock 模式下可留空）────────
    market_data_api_key: str = ""
    competitor_api_key: str = ""
    fx_api_key: str = ""
    order_api_base: str = ""
    order_api_key: str = ""
    logistics_api_base: str = ""
    logistics_api_key: str = ""
    inventory_api_base: str = ""
    inventory_api_key: str = ""
    refund_api_base: str = ""
    refund_api_key: str = ""
    translation_api_base: str = ""
    translation_api_key: str = ""
    ticket_api_base: str = ""
    ticket_api_key: str = ""
    # 推送渠道（周报 / 审批提醒）
    notify_email_from: str = "ops-bot@example.com"
    notify_webhook_url: str = ""

    # ── 领域默认参数 ──────────────────────────────────────────
    default_market: str = "SEA"  # 东南亚市场
    default_currency: str = "USD"
    default_fx_usd_cny: float = 7.2
    # 运费 / 平台佣金 / 广告费等用于利润测算的默认费率
    default_shipping_rate: float = 0.08
    default_commission_rate: float = 0.05
    default_ad_rate: float = 0.10

    # ── App ───────────────────────────────────────────────────
    environment: str = "development"
    log_level: str = "INFO"
    tool_timeout_seconds: int = 15
    tool_max_retries: int = 1
    allowed_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    @property
    def db_ssl(self) -> str | bool:
        """asyncpg 的 ssl 参数：disable 表示本地直连，require 表示云库强制 TLS。"""
        mode = (self.db_ssl_mode or "disable").lower()
        if mode in ("disable", "false", "0", ""):
            return False
        return mode


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if len(settings.jwt_secret) < 32:
        raise ValueError("JWT_SECRET must be at least 32 characters")
    return settings
