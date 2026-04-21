"""0.17 fetch_changes_since — incremental pull using a timestamp cursor."""

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
        result = await liquid.fetch_changes_since(
            adapter,
            "/orders",
            since="2025-12-01T00:00:00+00:00",
        )

        print("=== liquid.fetch_changes_since — incremental sync ===")
        print(f"  since:            {result.since.isoformat()}")
        print(f"  until:            {result.until.isoformat()}")
        print(f"  detection_method: {result.detection_method}")
        print(f"  timestamp_field:  {result.timestamp_field}")
        print(f"  pages fetched:    {result.pages_fetched}")
        print(f"  changed records:  {len(result.changed_records)}  (of {len(orders)} total)")
        print("\nNext tick: feed `result.until` back in as `since=`.")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
