-- ==============================================================
--  电商运营中台 —— 数据库 Schema（集中建表脚本）
--  由 services/db.py::init_db() 在启动时执行（幂等，IF NOT EXISTS）
--  全部使用 Python 生态可访问的 PostgreSQL，无外部依赖
-- ==============================================================

-- ── 账号与认证 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    email      TEXT UNIQUE NOT NULL,
    password   TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'user',   -- user / admin
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_jti  TEXT UNIQUE NOT NULL,
    revoked    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_jti ON sessions (token_jti);

CREATE TABLE IF NOT EXISTS user_api_keys (
    user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    groq_key   TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    model      TEXT DEFAULT 'llama-3.3-70b-versatile',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── 活动日志（登录 / 建任务等审计）─────────────────────────────
CREATE TABLE IF NOT EXISTS activity_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ==============================================================
--  电商运营中台 —— 领域业务表
-- ==============================================================

-- ── 运营任务（一次运营目标的完整生命周期）─────────────────────
CREATE TABLE IF NOT EXISTS operations_tasks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT,
    task_type   TEXT NOT NULL,              -- selection / logistics / pricing / cs / weekly_report / mixed
    goal        TEXT NOT NULL,              -- 用户下达的运营目标原文
    params      JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'created',  -- created/running/waiting_approval/completed/failed/cancelled
    dag         JSONB NOT NULL DEFAULT '[]',      -- 依赖感知的任务 DAG
    result      JSONB NOT NULL DEFAULT '{}',      -- Supervisor 汇总结果
    error       TEXT,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_operations_tasks_status ON operations_tasks (status);
CREATE INDEX IF NOT EXISTS idx_operations_tasks_user   ON operations_tasks (user_id);

-- ── Agent 执行记录（每个子任务一条）───────────────────────────
CREATE TABLE IF NOT EXISTS agent_executions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     UUID NOT NULL REFERENCES operations_tasks(id) ON DELETE CASCADE,
    subtask_id  TEXT NOT NULL,
    subtask     TEXT,
    agent       TEXT NOT NULL,              -- supervisor / selection / order_logistics / customer_service / pricing / weekly_report / critic
    status      TEXT NOT NULL DEFAULT 'running', -- running/completed/failed/skipped/awaiting_approval
    attempt     INTEGER NOT NULL DEFAULT 1,
    input       JSONB NOT NULL DEFAULT '{}',
    output      JSONB NOT NULL DEFAULT '{}',
    output_text TEXT,
    error       TEXT,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    started_at  TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agent_executions_task ON agent_executions (task_id);

-- ── 审批记录（HITL 护栏）──────────────────────────────────────
CREATE TABLE IF NOT EXISTS approvals (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id        UUID REFERENCES operations_tasks(id) ON DELETE CASCADE,
    subtask_id     TEXT,
    agent          TEXT NOT NULL,
    operation_type TEXT NOT NULL,           -- price_change / refund / bulk_reship / listing_toggle / inventory_update
    payload        JSONB NOT NULL DEFAULT '{}',
    amount_usd     NUMERIC(14,2) DEFAULT 0,
    required_level TEXT NOT NULL DEFAULT 'supervisor', -- auto / supervisor / manager
    status         TEXT NOT NULL DEFAULT 'pending',    -- pending/approved/rejected/cancelled
    reason         TEXT,
    decided_by     TEXT,
    requested_at   TIMESTAMPTZ DEFAULT NOW(),
    decided_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals (status);
CREATE INDEX IF NOT EXISTS idx_approvals_task   ON approvals (task_id);

-- ── 订单异常 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS order_exceptions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id       TEXT NOT NULL,
    platform       TEXT,                    -- shopee / lazada / amazon
    exception_type TEXT,                    -- 丢件 / 超时 / 拒收 / 地址错误 / 清关
    severity       TEXT DEFAULT 'medium',   -- low/medium/high
    detail         JSONB NOT NULL DEFAULT '{}',
    status         TEXT DEFAULT 'open',     -- open/resolved/escalated
    resolution     TEXT,
    task_id        UUID,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ── 物流单 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shipments (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id   TEXT NOT NULL,
    tracking_no TEXT,
    carrier    TEXT,
    status     TEXT,                        -- in_transit/delivered/lost/delayed/returned
    last_event TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_shipments_order ON shipments (order_id);

-- ── 定价历史 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pricing_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku         TEXT NOT NULL,
    old_price   NUMERIC(14,2),
    new_price   NUMERIC(14,2),
    currency    TEXT DEFAULT 'USD',
    change_pct  NUMERIC(8,2),
    reason      TEXT,
    approval_id UUID REFERENCES approvals(id) ON DELETE SET NULL,
    approved_by TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pricing_history_sku ON pricing_history (sku);

-- ── 客服工单 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS support_tickets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_no   TEXT NOT NULL,
    platform    TEXT,
    language    TEXT,                       -- en / th / id / vi / zh ...
    customer    TEXT,
    message     TEXT,
    category    TEXT,                       -- 物流/退换货/产品咨询/投诉/其他
    sentiment   TEXT,                       -- positive/neutral/negative
    draft_reply TEXT,
    status      TEXT DEFAULT 'open',
    task_id     UUID,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── 选品候选 ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS product_candidates (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    category          TEXT,
    market            TEXT,
    cost_usd          NUMERIC(14,2),
    price_usd         NUMERIC(14,2),
    est_margin_pct    NUMERIC(8,2),
    demand_score      NUMERIC(6,2),
    competition_score NUMERIC(6,2),
    score             NUMERIC(6,2),
    recommendation    TEXT,
    task_id           UUID,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- ── 通知 / 推送 ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id    UUID,
    channel    TEXT NOT NULL DEFAULT 'websocket', -- websocket/email/webhook
    recipient  TEXT,
    subject    TEXT,
    body       TEXT,
    status     TEXT DEFAULT 'pending',            -- pending/sent/failed
    created_at TIMESTAMPTZ DEFAULT NOW(),
    sent_at    TIMESTAMPTZ
);

-- ── 选品 / 竞品 / 库存 演示数据表(mock 模式下由 seed_demo 灌入) ──
CREATE TABLE IF NOT EXISTS inventory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku         TEXT NOT NULL,
    warehouse   TEXT,
    quantity    INTEGER NOT NULL DEFAULT 0,
    safety_stock INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS competitor_prices (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku         TEXT NOT NULL,
    platform    TEXT,
    market      TEXT,
    price       NUMERIC(14,2),
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sales_daily (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku           TEXT NOT NULL,
    market        TEXT,
    sale_date     DATE NOT NULL,
    units         INTEGER NOT NULL DEFAULT 0,
    revenue_usd   NUMERIC(14,2) NOT NULL DEFAULT 0
);
