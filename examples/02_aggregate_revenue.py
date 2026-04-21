"""0.15 aggregate — group + sum records server-side without returning rows."""

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
        result = await liquid.aggregate(
            adapter,
            "/orders",
            group_by="status",
            agg={"total_cents": "sum", "id": "count"},
        )

        print("=== liquid.aggregate — revenue by status ===")
        print(f"scanned {result['total_records_scanned']} records across {counter.count} HTTP pages")
        print(f"agent receives {len(result['groups'])} buckets (not {result['total_records_scanned']} rows):\n")
        print(f"  {'status':<12} {'orders':>8} {'revenue':>14}")
        for bucket in sorted(result["groups"], key=lambda b: -b["sum_total_cents"]):
            status = bucket["key"]["status"]
            count = bucket["count_id"]
            total = bucket["sum_total_cents"]
            print(f"  {status:<12} {count:>8} {'$' + format(total / 100, ',.2f'):>14}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
