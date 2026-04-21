"""0.17 fetch_until — auto-paginate until a predicate matches."""

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
        result = await liquid.fetch_until(
            adapter,
            "/orders",
            predicate={"status": "refunded"},
            max_pages=10,
        )

        print("=== liquid.fetch_until — stop at first refunded order ===")
        print(f"  matched:         {result.matched}")
        print(f"  stopped_reason:  {result.stopped_reason}")
        print(f"  pages fetched:   {result.pages_fetched} (cap was 10)")
        print(f"  records scanned: {result.records_scanned}")
        if result.matching_record:
            m = result.matching_record
            print(f"  first match:     {m['id']}  status={m['status']}  total_cents={m['total_cents']}")
        print("\nOne call, Liquid keeps pulling pages until the predicate hits.")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
