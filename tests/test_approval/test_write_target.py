from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryVault
from liquid.approval import (
    ApprovalConflictError,
    ApprovalManager,
    CLIApproverChannel,
    LiquidWriteTarget,
    StaticApprovalPolicy,
)
from liquid.models.adapter import AdapterConfig, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint


async def test_sqlite_existing_write_with_cli_approval_and_rollback(tmp_path, monkeypatch):
    path = tmp_path / "resource.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO settings VALUES (1, 'before')")
        db.commit()
    liquid = Liquid(llm=None, vault=InMemoryVault(), sink=CollectorSink())
    config = AdapterConfig(
        schema=APISchema(
            source_url=f"sqlite:///{path}",
            service_name="Settings",
            discovery_method="sqlite",
            auth=AuthRequirement(type="custom", tier="A"),
            endpoints=[
                Endpoint(
                    path="/settings",
                    protocol="sqlite",
                    transport_meta={
                        "table": "settings",
                        "columns": ["id", "value"],
                        "schema": None,
                    },
                )
            ],
        ),
        auth_ref="none",
        mappings=[],
        sync=SyncConfig(endpoints=["/settings"]),
    )

    async def read():
        with closing(sqlite3.connect(path)) as db:
            return {"value": db.execute("SELECT value FROM settings WHERE id = 1").fetchone()[0]}

    target = LiquidWriteTarget(
        liquid,
        config,
        "/settings",
        read=read,
        project=lambda before, request: {**before, **request},
        build_write=lambda before, after: {"op": "update", "values": after, "where": {"id": 1, **before}},
    )
    channel = CLIApproverChannel(verified_sender_id="local-operator")
    manager = ApprovalManager(
        targets={"settings": target},
        policy=StaticApprovalPolicy(
            {"agent": {"settings": {"read", "propose", "publish"}}, "operator": {"settings": {"read", "approve"}}},
            {("cli", "local-operator"): "operator"},
        ),
    )
    proposal = await manager.propose("settings", {"value": "after"}, "agent")
    with pytest.raises(ApprovalConflictError):
        await manager.publish(proposal.id, "agent")
    monkeypatch.setattr("builtins.input", lambda _: "approve")
    await manager.send_preview(proposal.id, "agent", "operator", channel)
    await channel.prompt()
    await manager.receive(channel)
    await manager.publish(proposal.id, "agent")
    assert await read() == {"value": "after"}
    rollback = await manager.rollback(proposal.id, "agent")
    await manager.send_preview(rollback.id, "agent", "operator", channel)
    await channel.prompt()
    await manager.receive(channel)
    await manager.publish(rollback.id, "agent")
    assert await read() == {"value": "before"}
