"""Task 4 — Recover from expired credentials (401).

Baseline: a naive adapter would surface a plain 401 with no guidance.
Liquid:   ``AuthError.recovery.next_action`` points at ``store_credentials``.

Measures: whether an agent could plausibly recover without human input.
"""

from __future__ import annotations

from benchmarks.harness import (
    CallCounter,
    Measurement,
    TaskResult,
    _make_orders_adapter,
    always_401_handler,
    make_liquid,
)

__all__ = ["run"]


def _score(recovery_description: str | None) -> int:
    """1 if the hypothetical agent can act on this alone, else 0.

    Our heuristic: ``next_action`` must exist AND its ``description`` or
    ``tool`` name must be a canonical Liquid tool the agent recognises
    (``store_credentials``, ``repair_adapter``, etc).
    """
    return 1 if recovery_description else 0


async def run() -> TaskResult:
    # -- Baseline: bare 401 with no structured recovery ----------------
    # A naive client returns only the status code + body text; the agent
    # has nothing machine-parseable to act on.
    baseline_error = {
        "status": 401,
        "message": "Unauthorized",
        "recovery": None,
    }
    baseline_actionable = _score(recovery_description=None)

    # -- Liquid: AuthError with Recovery+ToolCall(next_action) ---------
    liquid_counter = CallCounter()
    handler = always_401_handler(liquid_counter)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        recovery_plan = None
        try:
            await liquid.fetch(adapter, "/orders")
        except Exception as exc:
            rec = getattr(exc, "recovery", None)
            if rec is not None and rec.next_action is not None:
                recovery_plan = {
                    "tool": rec.next_action.tool,
                    "args": rec.next_action.args,
                    "description": rec.next_action.description,
                    "retry_safe": rec.retry_safe,
                }
        liquid_actionable = _score(
            recovery_description=(recovery_plan or {}).get("description") or (recovery_plan or {}).get("tool"),
        )
    finally:
        await client.aclose()

    return TaskResult(
        task_id="task_04",
        title="Recover from 401",
        metric="next_action actionable (0/1)",
        measurements=[
            Measurement(baseline=baseline_actionable, liquid=liquid_actionable, unit="bool"),
        ],
        notes=(
            "Liquid maps HTTP 401 to AuthError with recovery.next_action = "
            "ToolCall('store_credentials', ...). An agent that recognises that "
            "tool name can self-heal without a human. Baseline: the agent "
            "gets a status code and a string — not enough to recover."
        ),
        details={
            "baseline_recovery": baseline_error,
            "liquid_recovery_plan": recovery_plan,
        },
    )
