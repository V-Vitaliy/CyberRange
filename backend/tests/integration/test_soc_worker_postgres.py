"""Opt-in real PostgreSQL check of the SOC worker's transaction boundaries.

Requires CYBERRANGE_SOC_TEST_DATABASE_URL and CYBERRANGE_SOC_TEST_DATABASE_NAME.
Retains its uniquely identified synthetic lab, session and events. The model is
a double; no external services are contacted.
"""

import asyncio
import os
import secrets
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

TEST_URL = os.environ.get("CYBERRANGE_SOC_TEST_DATABASE_URL")
TEST_NAME = os.environ.get("CYBERRANGE_SOC_TEST_DATABASE_NAME")


@unittest.skipUnless(TEST_URL and TEST_NAME, "Explicit SOC test PostgreSQL required")
class PostgresSocWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_transaction_or_row_lock_is_held_during_analysis(self):
        os.environ["DATABASE_URL"] = TEST_URL
        os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
        from sqlalchemy import select, text
        from sqlalchemy.engine import make_url
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.agents.soc_agent import AnalysisOutput
        from app.core.queue_manager import LLMQueueManager
        from app.db.database import Base
        from app.db.database import engine as app_engine
        from app.db.models import GameSession, SecurityAuditLog
        from app.siem import soc_worker

        url = make_url(TEST_URL)
        self.assertEqual(url.database, TEST_NAME, "Explicit database confirmation mismatch")
        observer = create_async_engine(url, hide_parameters=True)
        lab = uuid4()
        log_ids = [uuid4() for _ in range(3)]
        async with observer.begin() as conn:
            self.assertEqual(await conn.scalar(text("SELECT current_database()")), TEST_NAME)
            await conn.run_sync(Base.metadata.create_all)
        async with soc_worker.async_session() as db:
            db.add(GameSession(id=uuid4(), team_id=uuid4(), lab_instance_id=lab, is_solo=True))
            db.add_all(
                SecurityAuditLog(id=log_id, lab_instance_id=lab, event_type="LLM_PROMPT", payload={"prompt": "hi"})
                for log_id in log_ids
            )
            await db.commit()

        observations = []

        async def analyze(state):
            # Runs while the worker waits for the model: nothing may hold a transaction or the row.
            async with observer.connect() as conn:
                idle_in_transaction = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                        "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
                    )
                )
                await conn.execute(
                    text("SELECT id FROM security_audit_logs WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": state["log_id"]},
                )
                await conn.rollback()
            observations.append(idle_in_transaction)
            return {"is_malicious": False, "reasoning": "synthetic verdict"}

        async def ours_done():
            async with soc_worker.async_session() as db:
                pending = await db.scalar(
                    select(SecurityAuditLog.id)
                    .where(SecurityAuditLog.id.in_(log_ids), SecurityAuditLog.investigated_at.is_(None))
                    .limit(1)
                )
            return pending is None

        graph = AsyncMock()
        graph.ainvoke.side_effect = analyze
        worker = asyncio.create_task(soc_worker.run_soc_analyst_loop(graph, LLMQueueManager()))
        try:
            for _ in range(60):
                if await ours_done():
                    break
                await asyncio.sleep(1)
        finally:
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

        self.assertTrue(observations)
        self.assertEqual(set(observations), {0}, "a transaction was open while the model ran")
        async with soc_worker.async_session() as db:
            rows = (await db.execute(select(SecurityAuditLog).where(SecurityAuditLog.id.in_(log_ids)))).scalars().all()
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertIsNotNone(row.investigated_at)
            self.assertEqual(row.payload, {"prompt": "hi", "soc_reasoning": "synthetic verdict"})

        # A second verdict for an investigated event is discarded.
        verdict = AnalysisOutput(is_malicious=True, reasoning="late duplicate")
        self.assertFalse(await soc_worker.save_verdict(log_ids[0], verdict))
        await observer.dispose()
        await app_engine.dispose()


if __name__ == "__main__":
    unittest.main()
