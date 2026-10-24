"""Opt-in real PostgreSQL check; retains its uniquely named test records.

Run separately from the mocked backend suite. Set both
CYBERRANGE_SETUP_TEST_DATABASE_URL and CYBERRANGE_SETUP_TEST_DATABASE_NAME.
No containers, database creation, teardown, or external model calls.
"""
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sys
import unittest
from uuid import UUID, uuid4


ROOT = Path(__file__).resolve().parents[3]
TEST_URL = os.environ.get("CYBERRANGE_SETUP_TEST_DATABASE_URL")
TEST_DATABASE = os.environ.get("CYBERRANGE_SETUP_TEST_DATABASE_NAME")


@unittest.skipUnless(TEST_URL and TEST_DATABASE, "Explicit test PostgreSQL required")
class PostgresSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_login_preservation_and_atomic_failures(self):
        # Set before importing application settings; never fall back to .env.
        os.environ["DATABASE_URL"] = TEST_URL
        os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
        sys.path.insert(0, str(ROOT / "backend"))
        sys.path.insert(0, str(ROOT))
        import httpx
        import jwt
        from sqlalchemy import inspect, select, text
        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import DBAPIError
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from app.core.security import verify_password
        from app.db.database import Base, engine as app_engine
        from app.db.models import ChatThread, GameSession, SecurityAuditLog, User
        from app.main import app
        from scripts.admin_setup import Options, SqlStore, seed_transaction
        from app.core.security import get_password_hash

        url = make_url(TEST_URL)
        self.assertEqual(url.drivername, "postgresql+asyncpg")
        self.assertEqual(url.database, TEST_DATABASE, "Test database confirmation mismatch")
        engine = create_async_engine(url, hide_parameters=True, connect_args={"timeout": 10})
        db_factory = async_sessionmaker(engine, expire_on_commit=False)
        prefix = "setup58_" + uuid4().hex
        lab_id = uuid4()
        red_name, blue_name = prefix + "_red", prefix + "_blue"
        # Whitespace, Unicode, and >72 UTF-8 bytes exercise the real login hash path.
        red_password = "  " + secrets.token_urlsafe(64) + "\u044f  "
        blue_password = secrets.token_urlsafe(32)
        env = dict(os.environ, RED_TEAM_PASSWORD=red_password,
                   BLUE_TEAM_PASSWORD=blue_password)
        cli_count = 0

        async def cli(lab=lab_id, red=red_name, blue=blue_name, extra=(), success=True,
                      process_env=None):
            nonlocal cli_count
            cli_count += 1
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(ROOT / "scripts/admin_setup.py"),
                "--lab-id", str(lab), "--red-username", red,
                "--blue-username", blue, *extra,
                cwd=ROOT, env=process_env or env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                self.fail("Setup CLI exceeded 60 seconds")
            output = (stdout + stderr).decode()
            for secret in (TEST_URL, url.password, red_password, blue_password):
                if secret:
                    self.assertFalse(secret in output, "CLI exposed a secret")
            if success:
                self.assertEqual(proc.returncode, 0, "Setup CLI failed (output withheld)")
                self.assertFalse(stderr, "Successful CLI emitted stderr")
                import json
                return json.loads(stdout)
            self.assertNotEqual(proc.returncode, 0, "Conflicting CLI succeeded")
            self.assertFalse(stdout, "Failed CLI emitted success output")
            self.assertIn("admin_setup:", stderr.decode())

        async def snapshot():
            async with engine.connect() as conn:
                names = await conn.run_sync(lambda c: inspect(c).get_table_names())
                rows = {}
                for table in Base.metadata.sorted_tables:
                    if table.name in names:
                        result = await conn.execute(select(table).order_by(*table.primary_key))
                        rows[table.name] = result.all()
                return sorted(names), rows

        class FailingStore(SqlStore):
            async def flush(self):
                await super().flush()
                # Actual PostgreSQL transaction error, after both users/session flush.
                await self.db.execute(text("SELECT 1 / 0"))

        async def transaction_failure():
            before = await snapshot()
            failed_lab = uuid4()
            with self.assertRaises(DBAPIError):
                await seed_transaction(
                    engine, AsyncSession, Base.metadata, text,
                    lambda db: FailingStore(db, User, GameSession, select),
                    Options(failed_lab, prefix + "_failred", prefix + "_failblue"),
                    lambda _: red_password, get_password_hash)
            self.assertTrue(before == await snapshot(),
                            "Failed transaction changed tables or committed records")

        try:
            asyncio.get_running_loop().set_debug(False)
            async with engine.connect() as conn:
                actual_db = await conn.scalar(text("SELECT current_database()"))
                self.assertEqual(actual_db, TEST_DATABASE)
                version = await conn.scalar(text("SHOW server_version"))
            before_tables, _ = await snapshot()
            # On an empty database this also proves transactional create_all rollback.
            await transaction_failure()
            first = await cli()
            self.assertTrue(all(first["created"].values()))
            self.assertEqual(first["lab_instance_id"], str(lab_id))
            self.assertEqual(first["team_id"], first["red_user_id"])
            tables, _ = await snapshot()
            self.assertTrue(set(Base.metadata.tables).issubset(tables))

            async with db_factory() as db:
                red = await db.get(User, UUID(first["red_user_id"]))
                blue = await db.get(User, UUID(first["blue_user_id"]))
                self.assertEqual((red.role, blue.role), ("red_team", "blue_team"))
                self.assertEqual((red.lab_instance_id, blue.lab_instance_id), (lab_id, lab_id))
                self.assertTrue(verify_password(red_password, red.password_hash))
                self.assertTrue(verify_password(blue_password, blue.password_hash))
                session = await db.get(GameSession, UUID(first["session_id"]))
                self.assertEqual(session.defense_budget, 5)
                session.defense_budget = 2
                session.is_solo = False
                session.system_prompt = "Integration progress retained"
                session.use_reranker = True
                session.rate_limit_enabled = True
                session.rate_limit_rpm = 7
                session.jwt_filter_enabled = True
                db.add(ChatThread(user_id=red.id, session_id=session.id,
                                  messages=[{"role": "user", "content": "saved progress"}]))
                db.add(SecurityAuditLog(lab_instance_id=lab_id, event_type="SETUP_TEST_PROGRESS",
                                       payload={"integration": True},
                                       investigated_at=datetime.now(timezone.utc),
                                       investigated_by=blue.id))
                await db.commit()

            progress = await snapshot()
            # Existing passwords must not be needed, replaced, or rehashed.
            repeat_env = dict(env)
            repeat_env.pop("RED_TEAM_PASSWORD")
            repeat_env.pop("BLUE_TEAM_PASSWORD")
            repeat = await cli(extra=("--budget", "99", "--is-solo"), process_env=repeat_env)
            self.assertFalse(any(repeat["created"].values()))
            for key in ("lab_instance_id", "session_id", "team_id", "red_user_id", "blue_user_id"):
                self.assertEqual(first[key], repeat[key])
            self.assertTrue(progress == await snapshot(), "Repeat changed existing data")

            # Exercise actual mounted HTTP routes, auth dependencies and DB repositories.
            # ASGITransport does not run lifespan: no Redis/Chroma/LLM startup.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://integration") as client:
                for side, name, password in (("red", red_name, red_password),
                                              ("blue", blue_name, blue_password)):
                    response = await client.post(f"/api/{side}/login",
                                                 json={"username": name, "password": password})
                    self.assertEqual(response.status_code, 200, "Real login failed")
                    token = response.json()["access_token"]
                    claims = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])
                    self.assertEqual(claims["lab_instance_id"], str(lab_id))
                    self.assertEqual(claims["role"], side + "_team")
                    if side == "red":
                        scope = jwt.decode(claims["lab_scope"], os.environ["JWT_SECRET"],
                                           algorithms=["HS256"], audience="red-lab-scope")
                        self.assertEqual(scope["lab_instance_id"], str(lab_id))
                    else:
                        profile = await client.get("/api/blue/me", headers={"Authorization": "Bearer " + token})
                        self.assertEqual(profile.status_code, 200)
                        self.assertEqual(profile.json()["session_id"], first["session_id"])
                    bad = await client.post(f"/api/{side}/login",
                                            json={"username": name, "password": "incorrect"})
                    self.assertEqual(bad.status_code, 401)
                for side, name, password in (("red", blue_name, blue_password),
                                              ("blue", red_name, red_password)):
                    denied = await client.post(f"/api/{side}/login",
                                               json={"username": name, "password": password})
                    self.assertEqual(denied.status_code, 403)
            async with db_factory() as db:
                events = (await db.execute(select(SecurityAuditLog).where(
                    SecurityAuditLog.lab_instance_id == lab_id,
                    SecurityAuditLog.event_type == "RED_TEAM_LOGIN"))).scalars().all()
                self.assertEqual(len(events), 1, "Red login audit was not persisted")

            # Every CLI conflict must leave all rows unchanged, including unrelated labs.
            for kwargs in (
                {"red": blue_name, "blue": prefix + "_unused"},
                {"lab": uuid4(), "blue": prefix + "_unused"},
                {"extra": ("--team-id", str(uuid4()))},
                {"blue": red_name},
            ):
                before = await snapshot()
                await cli(success=False, **kwargs)
                self.assertTrue(before == await snapshot(), "Conflict changed records")

            # A partial lab: existing blue/session with nondefault team, missing red.
            partial_lab, team = uuid4(), uuid4()
            partial_blue = prefix + "_partialblue"
            async with db_factory() as db:
                db.add(User(username=partial_blue, role="blue_team", lab_instance_id=partial_lab,
                            password_hash=get_password_hash(blue_password)))
                partial_session = GameSession(lab_instance_id=partial_lab, team_id=team,
                                              defense_budget=1, is_solo=False)
                db.add(partial_session)
                await db.commit()
                partial_id = partial_session.id
            partial = await cli(lab=partial_lab, red=prefix + "_partialred", blue=partial_blue)
            self.assertEqual(partial["created"], {"red_team": True, "blue_team": False, "session": False})
            self.assertEqual(partial["session_id"], str(partial_id))
            self.assertEqual(partial["team_id"], str(team))
            async with db_factory() as db:
                self.assertEqual((await db.get(GameSession, partial_id)).defense_budget, 1)
                db.add(GameSession(lab_instance_id=partial_lab, team_id=team))
                await db.commit()
            before = await snapshot()
            await cli(lab=partial_lab, red=prefix + "_partialred", blue=partial_blue, success=False)
            self.assertTrue(before == await snapshot(), "Ambiguous sessions changed data")
            await transaction_failure()

            # Integer overflow reaches a real INSERT failure through the CLI.
            before = await snapshot()
            await cli(lab=uuid4(), red=prefix + "_overflowred", blue=prefix + "_overflowblue",
                      extra=("--budget", "2147483648"), success=False)
            self.assertTrue(before == await snapshot(), "Failed CLI transaction left records")

            # Real connection failure must also yield sanitized CLI failure.
            failure_env = dict(env, DATABASE_URL=url.set(database=prefix + "_absent").render_as_string(hide_password=False))
            await cli(process_env=failure_env, success=False)
            print(f"PostgreSQL {version}; database={TEST_DATABASE}; CLI runs={cli_count}; "
                  f"initially_empty={not before_tables}; retained prefix={prefix}", flush=True)
        finally:
            await engine.dispose()
            await app_engine.dispose()


if __name__ == "__main__":
    unittest.main()
