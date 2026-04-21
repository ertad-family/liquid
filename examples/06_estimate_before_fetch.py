"""0.16 estimate_fetch — pre-flight size/cost prediction without an HTTP call."""

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

BUDGET_TOKENS = 1_000


async def main() -> None:
    orders = load_fixture("orders.json")
    counter = CallCounter()
    handler = paginated_offset_handler(orders, counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        est = await liquid.estimate_fetch(adapter, "/orders")

        print("=== liquid.estimate_fetch — zero HTTP calls made ===")
        print(f"  expected items:  {est.expected_items}")
        print(f"  expected bytes:  {est.expected_bytes:,}")
        print(f"  expected tokens: {est.expected_tokens:,}")
        print(f"  confidence:      {est.confidence}")
        print(f"  source:          {est.source}")
        print(f"  http calls used: {counter.count}\n")

        if (est.expected_tokens or 0) > BUDGET_TOKENS:
            print(f"Agent decision: over {BUDGET_TOKENS}-token budget — switch to aggregate/search.")
        else:
            print(f"Agent decision: under {BUDGET_TOKENS}-token budget — safe to fetch.")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
