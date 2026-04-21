"""0.16 max_tokens — cap response size, surface truncation via _meta."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.harness import (
    CallCounter,
    _make_orders_adapter,
    estimate_tokens,
    load_fixture,
    make_liquid,
    paginated_offset_handler,
)

BUDGET = 2_000


async def main() -> None:
    orders = load_fixture("orders.json")
    counter = CallCounter()
    handler = paginated_offset_handler(orders, counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        raw = await liquid.fetch(adapter, "/orders")
        capped = await liquid.fetch(adapter, "/orders", max_tokens=BUDGET, include_meta=True)

        data = capped["data"]
        meta = capped["_meta"]

        print(f"=== liquid.fetch(max_tokens={BUDGET}, include_meta=True) ===")
        print(f"  raw response:     {len(raw)} items, ~{estimate_tokens(raw):,} tokens")
        print(f"  capped response:  {len(data)} items, ~{estimate_tokens(capped):,} tokens")
        print(f"  _meta.truncated:  {meta['truncated']}")
        print(f"  _meta.source:     {meta['source']}")
        print(f"  _meta.adapter:    {meta['adapter']}")
        print("\nAgent sees the cap, knows to paginate if it needs more.")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
