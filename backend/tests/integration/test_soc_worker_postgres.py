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
    async def asyncTearDown(self):
        # Pooled connections belong to this test's event loop; the next test runs in a new one.
        from app.db.database import engine, siem_engine

        await engine.dispose()
        await siem_engine.dispose()

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

    async def test_login_events_are_closed_by_rule_and_never_reach_the_model(self):
        os.environ["DATABASE_URL"] = TEST_URL
        os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
        from sqlalchemy import select, text
        from sqlalchemy.engine import make_url
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.queue_manager import LLMQueueManager
        from app.db.database import Base
        from app.db.database import engine as app_engine
        from app.db.models import GameSession, SecurityAuditLog
        from app.siem import soc_worker

        url = make_url(TEST_URL)
        self.assertEqual(url.database, TEST_NAME, "Explicit database confirmation mismatch")
        observer = create_async_engine(url, hide_parameters=True)
        async with observer.begin() as conn:
            self.assertEqual(await conn.scalar(text("SELECT current_database()")), TEST_NAME)
            await conn.run_sync(Base.metadata.create_all)
        await observer.dispose()
        solo, team = uuid4(), uuid4()
        burst, single, uncounted, prompt, team_event = (uuid4() for _ in range(5))
        async with soc_worker.async_session() as db:
            db.add(GameSession(id=uuid4(), team_id=uuid4(), lab_instance_id=solo, is_solo=True))
            db.add(GameSession(id=uuid4(), team_id=uuid4(), lab_instance_id=team, is_solo=False))
            for log_id, lab, event_type, payload in (
                (burst, solo, "LOGIN_FAILED", {"username": "a", "failed_usernames_from_source": 6}),
                (single, solo, "LOGIN_FAILED", {"username": "b", "failed_usernames_from_source": 1}),
                (prompt, solo, "LLM_PROMPT", {"prompt": "hi"}),
                (team_event, team, "LOGIN_FAILED", {"username": "c", "failed_usernames_from_source": 6}),
                # Counting failed when this was logged: the rule cannot judge it.
                (uncounted, solo, "LOGIN_FAILED", {"username": "d", "failed_usernames_from_source": None}),
            ):
                db.add(SecurityAuditLog(id=log_id, lab_instance_id=lab, event_type=event_type, payload=payload))
            await db.commit()

        analyzed = []

        async def analyze(state):
            analyzed.append(state["event_type"])
            return {"is_malicious": False, "reasoning": "synthetic verdict"}

        async def rows():
            async with soc_worker.async_session() as db:
                result = await db.execute(
                    select(SecurityAuditLog).where(
                        SecurityAuditLog.id.in_([burst, single, uncounted, prompt, team_event])
                    )
                )
                return {row.id: row for row in result.scalars().all()}

        graph = AsyncMock()
        graph.ainvoke.side_effect = analyze
        worker = asyncio.create_task(soc_worker.run_soc_analyst_loop(graph, LLMQueueManager()))
        try:
            for _ in range(60):
                current = await rows()
                if all(current[log_id].investigated_at for log_id in (burst, single, prompt)):
                    break
                await asyncio.sleep(1)
        finally:
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

        current = await rows()
        self.assertNotIn("LOGIN_FAILED", analyzed, "a login event reached the model")
        self.assertIn("LLM_PROMPT", analyzed)
        self.assertTrue(current[burst].is_compromised)
        self.assertFalse(current[single].is_compromised)
        self.assertTrue(current[burst].payload["soc_reasoning"].startswith("Rule-based verdict"))
        # Team labs keep their events for the Blue Team to investigate.
        self.assertIsNone(current[team_event].investigated_at)
        # Without a count the event stays pending instead of being closed as benign.
        self.assertIsNone(current[uncounted].investigated_at)
        await app_engine.dispose()

    async def test_concurrent_failed_logins_from_one_source_count_each_other(self):
        """Without serialization, a parallel burst would store count 1 everywhere and look benign."""
        os.environ["DATABASE_URL"] = TEST_URL
        os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
        from sqlalchemy import select, text
        from sqlalchemy.engine import make_url
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.db.database import Base
        from app.db.database import engine as app_engine
        from app.db.models import SecurityAuditLog
        from app.siem import service as siem

        url = make_url(TEST_URL)
        self.assertEqual(url.database, TEST_NAME, "Explicit database confirmation mismatch")
        observer = create_async_engine(url, hide_parameters=True)
        async with observer.begin() as conn:
            self.assertEqual(await conn.scalar(text("SELECT current_database()")), TEST_NAME)
            await conn.run_sync(Base.metadata.create_all)
        await observer.dispose()
        lab = str(uuid4())
        names = [f"probe{i}" for i in range(8)]
        await asyncio.gather(
            *(
                siem.log_failed_login(lab_instance_id=lab, username=name, source_ip="198.51.100.20", portal="red")
                for name in names
            )
        )
        async with siem.siem_session() as db:
            rows = (
                (
                    await db.execute(
                        select(SecurityAuditLog).where(
                            SecurityAuditLog.lab_instance_id == lab, SecurityAuditLog.event_type == "LOGIN_FAILED"
                        )
                    )
                )
                .scalars()
                .all()
            )
        counts = sorted(row.payload["failed_usernames_from_source"] for row in rows)
        self.assertEqual(counts, list(range(1, len(names) + 1)))
        await app_engine.dispose()

    async def test_events_are_written_while_the_request_pool_is_exhausted(self):
        """Every request-pool connection is held, as by requests that log events: writes must not wait."""
        os.environ["DATABASE_URL"] = TEST_URL
        os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
        from sqlalchemy import select, text

        from app.db import database
        from app.db.models import SecurityAuditLog
        from app.siem import service as siem

        async with database.engine.begin() as conn:
            self.assertEqual(await conn.scalar(text("SELECT current_database()")), TEST_NAME)
            await conn.run_sync(database.Base.metadata.create_all)
        pool = database.engine.pool
        held = []
        lab = str(uuid4())
        try:
            for _ in range(pool.size() + pool._max_overflow):
                conn = await database.engine.connect()
                await conn.execute(text("SELECT 1"))
                held.append(conn)
            event = await asyncio.wait_for(siem.log_security_event(lab, "POOL_CHECK", {"k": "v"}), timeout=5)
            self.assertIsNotNone(event)
        finally:
            for conn in held:
                await conn.close()
        async with siem.siem_session() as db:
            stored = await db.scalar(select(SecurityAuditLog.event_type).where(SecurityAuditLog.lab_instance_id == lab))
        self.assertEqual(stored, "POOL_CHECK")


if __name__ == "__main__":
    unittest.main()
