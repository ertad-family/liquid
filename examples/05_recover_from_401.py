"""0.12 Recovery — AuthError carries a machine-readable next_action."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.harness import (
    CallCounter,
    _make_orders_adapter,
    always_401_handler,
    make_liquid,
)


async def main() -> None:
    counter = CallCounter()
    handler = always_401_handler(counter)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        try:
            await liquid.fetch(adapter, "/orders")
        except Exception as exc:
            recovery = getattr(exc, "recovery", None)
            print("=== 401 caught — inspecting structured recovery ===")
            print(f"  exception:   {type(exc).__name__}: {exc}")
            print(f"  retry_safe:  {recovery.retry_safe if recovery else None}")
            if recovery and recovery.next_action:
                na = recovery.next_action
                print(f"  next tool:   {na.tool}")
                print(f"  args:        {na.args}")
                print(f"  description: {na.description}")
                print("\nThe agent can dispatch next_action.tool directly — no string parsing.")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
