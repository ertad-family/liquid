"""A human approves each SQLite settings change. No network, credentials or LLM.

Run from the repository: python examples/approval_cli.py
"""

from __future__ import annotations

import asyncio
import getpass
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryVault
from liquid.approval import (
    ApprovalManager,
    CLIApproverChannel,
    LiquidWriteTarget,
    SQLiteApprovalStore,
    StaticApprovalPolicy,
)
from liquid.models.adapter import AdapterConfig, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint


async def main() -> None:
    with TemporaryDirectory(prefix="liquid-approval-") as directory:
        resource = Path(directory) / "settings.sqlite3"
        with closing(sqlite3.connect(resource)) as db:
            db.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO settings VALUES (1, 'before')")
            db.commit()
        liquid = Liquid(llm=None, vault=InMemoryVault(), sink=CollectorSink())
        config = AdapterConfig(
            schema=APISchema(
                service_name="Local demo",
                source_url=f"sqlite:///{resource}",
                discovery_method="sqlite",
                auth=AuthRequirement(type="custom", tier="A"),
                endpoints=[
                    Endpoint(
                        path="/settings",
                        protocol="sqlite",
                        transport_meta={"table": "settings", "schema": None, "columns": ["id", "value"]},
                    )
                ],
            ),
            auth_ref="none",
            mappings=[],
            sync=SyncConfig(endpoints=["/settings"]),
        )

        async def read() -> dict:
            with closing(sqlite3.connect(resource)) as db:
                return {"value": db.execute("SELECT value FROM settings WHERE id = 1").fetchone()[0]}

        target = LiquidWriteTarget(
            liquid,
            config,
            "/settings",
            read=read,
            project=lambda before, request: {**before, **request},
            build_write=lambda before, after: {"op": "update", "values": after, "where": {"id": 1, **before}},
        )
        operator = getpass.getuser()  # trusted local operator process; never a model argument
        channel = CLIApproverChannel(operator)
        manager = ApprovalManager(
            targets={"demo": target},
            store=SQLiteApprovalStore(Path(directory) / "approval.sqlite3"),
            policy=StaticApprovalPolicy(
                {"agent": {"demo": {"read", "propose", "publish"}}, "human": {"demo": {"read", "approve"}}},
                {("cli", operator): "human"},
            ),
        )
        proposal = await manager.propose("demo", {"value": "after"}, "agent")
        await manager.send_preview(proposal.id, "agent", "human", channel)
        await channel.prompt()
        decision = await manager.receive(channel)
        if decision and decision.status == "approved":
            await manager.publish(proposal.id, "agent")
            print("Published:", await read())
            rollback = await manager.rollback(proposal.id, "agent")
            await manager.send_preview(rollback.id, "agent", "human", channel)
            await channel.prompt()
            decision = await manager.receive(channel)
            if decision and decision.status == "approved":
                await manager.publish(rollback.id, "agent")
                print("Restored:", await read())
        else:
            print("No change published:", await read())


if __name__ == "__main__":
    asyncio.run(main())
