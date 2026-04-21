"""0.17 search_nl — LLM translates natural-language queries into the DSL."""

from __future__ import annotations

import asyncio
import json
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
    canned_dsl = json.dumps({"status": "paid", "total_cents": {"$gt": 50000}})
    liquid, client, llm = await make_liquid(handler, canned_llm_response=canned_dsl)
    try:
        adapter = _make_orders_adapter()
        result = await liquid.search_nl(
            adapter,
            "/orders",
            "paid orders over $500",
            limit=5,
            fields=["id", "status", "total_cents"],
        )

        print('=== liquid.search_nl("paid orders over $500") ===')
        print(f"  llm provider:   {result.llm_provider}")
        print(f"  llm calls made: {llm.calls}")
        print(f"  compiled DSL:   {result.compiled_query}")
        print(f"  returned:       {len(result.records)} records\n")
        for r in result.records:
            print(f"    {r['id']}  {r['status']}  ${r['total_cents'] / 100:,.2f}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
