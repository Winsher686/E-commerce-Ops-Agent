# backend/tests/conftest.py
# ──────────────────────────────────────────────────────────────
#  pytest 夹具与测试环境变量
#  说明：在导入任何 backend 模块前设置必需的环境变量，
#        使 config.get_settings() 可以正常初始化（无需真实数据库）。
# ──────────────────────────────────────────────────────────────
import os
import sys

# 必需的配置项（避免 pydantic 校验失败）
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("JWT_SECRET", "x" * 48)
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("SERPAPI_KEY", "test-serpapi-key")
os.environ.setdefault("MOCK_ENABLED", "true")
os.environ.setdefault("HITL_ENABLED", "true")

# 让测试可直接 import backend 下的模块
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
