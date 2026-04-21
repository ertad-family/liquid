"""0.10 query DSL — filter records server-side with MongoDB-style operators."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.harness import (
    CallCounter,
    _make_orders_adapter,
    load_fixture,
    make_liquid,
    paginated_offset_handler,
)


async def main() -> None:
    orders = load_fixture("orders.json")
    counter = CallCounter()
    handler = paginated_offset_handler(orders, counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        resp = await liquid.search(
            adapter,
            "/orders",
            where={"total_cents": {"$gt": 10000}, "status": "paid"},
            fields=["id", "status", "total_cents"],
            limit=5,
        )

        print("=== liquid.search — paid orders over $100 ===")
        print(f"scanned: {resp.meta.total_items}  matched (top {len(resp.items)}):")
        for r in resp.items:
            print(f"  {r['id']}  status={r['status']}  total=${r['total_cents'] / 100:,.2f}")
        print(f"\ntokens returned to agent: ~{resp.meta.estimated_tokens}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
