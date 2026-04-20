"""Task 3 — Fetch just id + email from a fat customer record.

Baseline: agent receives the full customer JSON (~40 fields).
Liquid-fields: ``fetch_with_meta(..., fields=["id", "email"])``.
Liquid-terse:  ``fetch(..., verbosity="terse")``.

Measures: tokens returned.
"""

from __future__ import annotations

from benchmarks.harness import (
    CallCounter,
    Measurement,
    TaskResult,
    _make_customer_adapter,
    estimate_tokens,
    load_fixture,
    make_liquid,
    single_record_handler,
)


async def run() -> TaskResult:
    customer = load_fixture("customer.json")

    # -- Baseline: full record ------------------------------------------
    counter = CallCounter()
    handler = single_record_handler(customer, counter)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_customer_adapter()
        result = await liquid.fetch(adapter, "/customers/123")
        # Liquid.fetch returns a list even for single-item endpoints when the
        # response is a bare dict (our mock); normalize to one record.
        record = result[0] if isinstance(result, list) and result else result
        baseline_tokens = estimate_tokens(record)
        baseline_fields = len(record) if isinstance(record, dict) else 0
    finally:
        await client.aclose()

    # -- Liquid: fields=[id, email] -------------------------------------
    counter = CallCounter()
    handler = single_record_handler(customer, counter)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_customer_adapter()
        resp = await liquid.fetch_with_meta(adapter, "/customers/123", fields=["id", "email"])
        liquid_fields_tokens = estimate_tokens(resp.items)
        liquid_fields_count = len(resp.items[0]) if resp.items else 0
    finally:
        await client.aclose()

    # -- Liquid: verbosity="terse" --------------------------------------
    counter = CallCounter()
    handler = single_record_handler(customer, counter)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_customer_adapter()
        result = await liquid.fetch(adapter, "/customers/123", verbosity="terse")
        record = result[0] if isinstance(result, list) and result else result
        liquid_terse_tokens = estimate_tokens(result)
        liquid_terse_count = len(record) if isinstance(record, dict) else 0
    finally:
        await client.aclose()

    return TaskResult(
        task_id="task_03",
        title="Fetch customer (id+email only)",
        metric="tokens returned to agent",
        measurements=[
            Measurement(baseline=baseline_tokens, liquid=liquid_fields_tokens, unit="tokens"),
            Measurement(baseline=baseline_fields, liquid=liquid_fields_count, unit="fields"),
        ],
        notes=(
            "fields=['id','email'] drops every field the agent doesn't need. "
            "verbosity='terse' is an alternative with comparable savings but "
            "a different selection heuristic."
        ),
        details={
            "baseline_fields": baseline_fields,
            "fields_tokens": liquid_fields_tokens,
            "terse_tokens": liquid_terse_tokens,
            "terse_fields": liquid_terse_count,
        },
    )
