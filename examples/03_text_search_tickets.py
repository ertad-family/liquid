"""0.15 text_search — BM25-lite relevance scoring across string fields."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.harness import (
    CallCounter,
    _make_tickets_adapter,
    load_fixture,
    make_liquid,
    paginated_offset_handler,
)


async def main() -> None:
    tickets = load_fixture("tickets.json")
    counter = CallCounter()
    handler = paginated_offset_handler(tickets, counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_tickets_adapter()
        ranked = await liquid.text_search(
            adapter,
            "/tickets",
            "shipping warehouse carrier",
            fields=["subject", "body"],
            limit=3,
        )

        print(f"=== liquid.text_search — {len(tickets)} tickets, top 3 by relevance ===\n")
        for hit in ranked:
            rec = hit["record"]
            score = hit["score"]
            matched = ", ".join(hit["matched_fields"])
            print(f"  [{score:.3f}] {rec['id']}  (matched: {matched})")
            print(f"         {rec['subject']}")
            print()
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
