#!/usr/bin/env bash
# ================================================================
#  跨境电商运营中台（CrossBorder Ops Agent）— 开发环境初始化脚本
#  克隆后执行一次: bash setup.sh
# ================================================================
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
error() { echo -e "${RED}[error]${NC} $*"; exit 1; }

# ── 1. 环境检查 ────────────────────────────────────────────────
info "Checking prerequisites..."
command -v python3 >/dev/null || error "需要 Python 3.11+，请从 https://python.org 安装"
command -v docker  >/dev/null || warn  "未检测到 Docker — 需要它来启动 Postgres 与 Redis"

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python version: $PYTHON_VERSION"

# ── 2. 生成 .env ───────────────────────────────────────────────
if [ ! -f ".env" ]; then
  cp .env.example .env
  warn ".env 已从 .env.example 生成 — 运行前请填写 GROQ_API_KEY / JWT_SECRET！"
else
  info ".env 已存在，跳过。"
fi

# ── 3. Python 虚拟环境与依赖 ───────────────────────────────────
info "创建虚拟环境 backend/.venv ..."
python3 -m venv backend/.venv

info "安装 Python 依赖..."
source backend/.venv/bin/activate

pip install --upgrade pip --quiet
pip install -r backend/requirements.txt

info "Python 依赖安装完成。"

# ── 4. 生成演示数据（可选）────────────────────────────────────
info "可执行以下命令灌入演示数据（订单/库存/工单/竞品价/选品）："
info "    source backend/.venv/bin/activate && cd backend && python seed_demo.py"

# ── 5. 下一步提示 ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo -e "${GREEN} Setup complete! Next steps:${NC}"
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo ""
echo "  1. 编辑 .env，至少填写："
echo "       GROQ_API_KEY=...      # 默认 LLM（Groq）"
echo "       JWT_SECRET=...        # 至少 32 字符"
echo "     本地 Docker 的 Postgres 保持 DB_SSL_MODE=disable"
echo "     MOCK_ENABLED=true 时全部领域工具走本地模拟数据，无需任何外部 API Key"
echo ""
echo "  2. 启动 Postgres + Redis（建表由后端启动时自动完成）:"
echo "       docker compose up -d"
echo ""
echo "  3. 启动后端:"
echo "       source backend/.venv/bin/activate"
echo "       cd backend && uvicorn main:app --reload"
echo ""
echo "  4. 打开 API 文档验证服务:"
echo "       http://localhost:8000/docs"
echo ""
echo "  5. 跑测试:"
echo "       cd backend && pytest"
echo ""
