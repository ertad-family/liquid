"""Task 5 — Find the ticket about a shipping problem.

Baseline: agent fetches all 200 tickets, keyword-matches in memory.
Liquid:   ``liquid.text_search(adapter, "/tickets", "shipping")``.

Measures: tokens returned, recall (target ticket in top-5?).
"""

from __future__ import annotations

from benchmarks.harness import (
    CallCounter,
    Measurement,
    TaskResult,
    _make_tickets_adapter,
    estimate_tokens,
    load_fixture,
    make_liquid,
    paginated_offset_handler,
)

TARGET_TICKET_ID = "tkt_0042"  # planted in fixtures/_generate.py


async def run() -> TaskResult:
    tickets = load_fixture("tickets.json")

    # -- Baseline: fetch all pages, agent searches locally --------------
    baseline_counter = CallCounter()
    handler = paginated_offset_handler(tickets, baseline_counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        from liquid.query._paginator import _walk_pages

        adapter = _make_tickets_adapter()
        all_seen: list[dict] = []
        async for page in _walk_pages(liquid, adapter, "/tickets"):
            all_seen.extend(page)
        baseline_tokens = estimate_tokens(all_seen)
        # Simulate a cheap keyword match: everything containing 'shipping' in
        # subject or body. An agent still has to ingest all_seen to run it.
        matches = [t for t in all_seen if "shipping" in (t["subject"] + " " + t["body"]).lower()]
        baseline_in_top5 = any(t["id"] == TARGET_TICKET_ID for t in matches[:5])
    finally:
        await client.aclose()

    # -- Liquid: text_search returns scored top-N -----------------------
    liquid_counter = CallCounter()
    handler = paginated_offset_handler(tickets, liquid_counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_tickets_adapter()
        ranked = await liquid.text_search(
            adapter,
            "/tickets",
            "shipping",
            fields=["subject", "body"],
            limit=5,
        )
        liquid_tokens = estimate_tokens(ranked)
        # text_search returns [{"record": {...}, "score": float, "matched_fields": [...]}]
        liquid_in_top5 = any(r.get("record", {}).get("id") == TARGET_TICKET_ID for r in ranked[:5])
    finally:
        await client.aclose()

    return TaskResult(
        task_id="task_05",
        title="Find the shipping ticket",
        metric="tokens returned to agent",
        measurements=[
            Measurement(baseline=baseline_tokens, liquid=liquid_tokens, unit="tokens"),
            Measurement(
                baseline=1 if baseline_in_top5 else 0,
                liquid=1 if liquid_in_top5 else 0,
                unit="bool",
            ),
        ],
        notes=(
            "text_search walks pages inside Liquid and returns the top N scored "
            "records; the agent never sees the other 195. Both variants locate "
            "the planted target within their top-5 — Liquid does it with a "
            "tiny fraction of the payload."
        ),
        details={
            "total_tickets": len(tickets),
            "target_id": TARGET_TICKET_ID,
            "liquid_top5_ids": [
                r.get("record", {}).get("id") for r in (ranked[:5] if isinstance(ranked, list) else [])
            ],
        },
    )
