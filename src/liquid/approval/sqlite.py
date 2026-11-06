"""Durable single-host storage, with crash-released cross-process resource locks.

The lock database is separate from records: the backup can commit durably BEFORE
an upstream write while the resource remains locked. No background workers.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

from liquid.approval.models import AuditRecord, Proposal, digest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _secure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)


class SQLiteApprovalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.lock_dir = self.path.with_suffix(self.path.suffix + ".locks")
        _secure_file(self.path)
        self.lock_dir.mkdir(mode=0o700, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY, target TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (channel TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY(channel, id));
                CREATE TABLE IF NOT EXISTS knowledge (client TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, isolation_level=None)

    @asynccontextmanager
    async def lock(self, target: str) -> AsyncIterator[None]:
        path = self.lock_dir / (digest(target) + ".sqlite3")
        _secure_file(path)
        with closing(sqlite3.connect(path, timeout=0, isolation_level=None)) as db:
            while True:
                try:
                    db.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        raise
                    await asyncio.sleep(0.025)
            try:
                yield
            finally:
                db.rollback()

    async def get(self, proposal_id: str) -> Proposal:
        with closing(self._connect()) as db:
            row = db.execute("SELECT body FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return Proposal.model_validate_json(row[0])

    async def save(self, proposal: Proposal) -> None:
        with closing(self._connect()) as db:
            db.execute("INSERT OR REPLACE INTO proposals VALUES (?, ?)", (proposal.id, proposal.model_dump_json()))

    async def put_audit(self, record: AuditRecord) -> None:
        with closing(self._connect()) as db:
            db.execute(
                "INSERT OR REPLACE INTO audit VALUES (?, ?, ?)", (record.id, record.target, record.model_dump_json())
            )

    async def audits(self, target: str) -> list[AuditRecord]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT body FROM audit WHERE target = ? ORDER BY rowid", (target,)).fetchall()
        return [AuditRecord.model_validate_json(row[0]) for row in rows]

    async def consume_event(self, channel: str, event_id: str) -> bool:
        with closing(self._connect()) as db:
            return db.execute("INSERT OR IGNORE INTO events VALUES (?, ?)", (channel, event_id)).rowcount == 1


class SQLiteKnowledgeStateStore:
    """The preset's source of truth; may share its database with the audit store."""

    def __init__(self, path: str | Path) -> None:
        self.store = SQLiteApprovalStore(path)

    async def load(self, client_id: str) -> dict[str, Any]:
        with closing(self.store._connect()) as db:
            row = db.execute("SELECT body FROM knowledge WHERE client = ?", (client_id,)).fetchone()
        if row is None:
            raise KeyError(client_id)
        return json.loads(row[0])

    async def save(self, client_id: str, state: dict[str, Any]) -> None:
        with closing(self.store._connect()) as db:
            db.execute(
                "INSERT OR REPLACE INTO knowledge VALUES (?, ?)",
                (client_id, json.dumps(state, ensure_ascii=False, allow_nan=False)),
            )
