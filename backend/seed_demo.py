# backend/seed_demo.py
# ──────────────────────────────────────────────────────────────
#  演示数据灌库脚本
#  用法：cd backend && python seed_demo.py
#  将 sample_data/*.json 写入 PostgreSQL 领域业务表，便于演示与联调。
# ──────────────────────────────────────────────────────────────
import asyncio
import json
import os

from services.db import init_db, get_db

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_data")

# 表名 → (JSON 字段, 目标表列)
_TABLES = {
    "sales_daily": (["sku", "market", "sale_date", "units", "revenue_usd"],
                    ["sku", "market", "sale_date", "units", "revenue_usd"]),
    "order_exceptions": (["order_id", "platform", "exception_type", "severity", "status"],
                         ["order_id", "platform", "exception_type", "severity", "status"]),
    "support_tickets": (["ticket_no", "platform", "language", "customer", "message",
                         "category", "sentiment", "draft_reply", "status"],
                        ["ticket_no", "platform", "language", "customer", "message",
                         "category", "sentiment", "draft_reply", "status"]),
    "pricing_history": (["sku", "old_price", "new_price", "currency", "change_pct", "reason"],
                        ["sku", "old_price", "new_price", "currency", "change_pct", "reason"]),
    "product_candidates": (["name", "category", "market", "cost_usd", "price_usd",
                            "est_margin_pct", "demand_score", "competition_score",
                            "score", "recommendation"],
                           ["name", "category", "market", "cost_usd", "price_usd",
                            "est_margin_pct", "demand_score", "competition_score",
                            "score", "recommendation"]),
    "inventory": (["sku", "warehouse", "quantity", "safety_stock"],
                  ["sku", "warehouse", "quantity", "safety_stock"]),
    "competitor_prices": (["sku", "platform", "market", "price"],
                          ["sku", "platform", "market", "price"]),
}


async def _seed(conn, table: str, rows: list[dict], fields: list[str], cols: list[str]) -> int:
    if not rows:
        return 0
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_sql = ", ".join(cols)
    n = 0
    for r in rows:
        values = []
        for f in fields:
            v = r.get(f)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            values.append(v)
        await conn.execute(
            f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})",
            *values,
        )
        n += 1
    return n


async def main():
    await init_db()
    pool = await get_db()
    total = 0
    async with pool.acquire() as conn:
        for table, (fields, cols) in _TABLES.items():
            path = os.path.join(SAMPLE_DIR, f"{table}.json")
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                rows = json.load(f)
            n = await _seed(conn, table, rows, fields, cols)
            total += n
            print(f"  + {table}: {n} 行")
    print(f"演示数据灌入完成，共 {total} 行。")


if __name__ == "__main__":
    asyncio.run(main())
