"""Opt-in PostgreSQL boundary checks. Retains uniquely named synthetic fixtures.

Requires CYBERRANGE_SQLI_TEST_DATABASE_URL and CYBERRANGE_SQLI_TEST_DATABASE_NAME.
No .env fallback, shared data reset, real flags, external models, or service mocks.
Run separately with the repository's Python environment.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from uuid import uuid4


TEST_URL = os.environ.get('CYBERRANGE_SQLI_TEST_DATABASE_URL')
TEST_NAME = os.environ.get('CYBERRANGE_SQLI_TEST_DATABASE_NAME')


@unittest.skipUnless(TEST_URL and TEST_NAME, 'Explicit SQLi test PostgreSQL required')
class PostgresIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_roles_views_and_denied_escape(self):
        os.environ['DATABASE_URL'] = TEST_URL
        os.environ['JWT_SECRET'] = secrets.token_urlsafe(48)
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'backend'))
        from sqlalchemy import text
        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import DBAPIError
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.db.database import Base, engine as app_engine
        from app.core.config import settings
        from app.db.models import ChatThread, GameSession, SecurityAuditLog, User
        from app.db.sqli_isolation import (
            IsolationError, LabObjects, check_reader_boundary, provision_lab,
        )

        asyncio.get_running_loop().set_debug(False)
        url = make_url(TEST_URL)
        self.assertEqual(url.drivername, 'postgresql+asyncpg')
        self.assertEqual(url.database, TEST_NAME, 'Explicit database confirmation mismatch')
        self.assertEqual(make_url(settings.DATABASE_URL), url,
                         'Run this integration check separately from already-imported app settings')
        admin = create_async_engine(url, hide_parameters=True, connect_args={'timeout': 10})
        readers = []
        labs = [uuid4(), uuid4()]
        objects = []
        identities = []
        sessions = []
        marker = 'sqli59_' + uuid4().hex
        password = secrets.token_urlsafe(32) + "'\\:unicode_я"
        try:
            async with admin.begin() as conn:
                self.assertEqual(await conn.scalar(text('SELECT current_database()')), TEST_NAME)
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(admin, expire_on_commit=False)() as db:
                for index, lab in enumerate(labs):
                    user = User(id=uuid4(), username=f'{marker}_{index}',
                                password_hash='synthetic-not-a-real-password-hash',
                                role='red_team', lab_instance_id=lab)
                    session = GameSession(id=uuid4(), team_id=user.id, lab_instance_id=lab,
                                          system_prompt='synthetic-control-plane-private')
                    db.add_all([user, session])
                    await db.flush()
                    identities.append(dict(id=str(user.id), sub=user.username,
                                           lab_instance_id=str(lab), role='red_team'))
                    sessions.append(str(session.id))
                    db.add(ChatThread(id=uuid4(), user_id=user.id, session_id=session.id,
                                      title=f'normal history {index}',
                                      messages=[{'content': f'{marker}_chat_{index}'}]))
                    db.add(SecurityAuditLog(lab_instance_id=lab, event_type='SQLI_TEST',
                                            payload={'message': f'{marker}_audit_{index}'}))
                await db.commit()
            for lab in labs:
                async with admin.begin() as conn:
                    objects.append(await provision_lab(conn, lab, password))
            for obj in objects:
                reader = create_async_engine(url.set(username=obj.reader, password=password),
                                             hide_parameters=True, connect_args={'timeout': 10})
                readers.append(reader)
                async with reader.connect() as conn:
                    self.assertEqual(await conn.scalar(text('SELECT session_user')), obj.reader)
                    await check_reader_boundary(conn, obj)

            first, second = objects
            reader = readers[0]
            async with reader.connect() as conn:
                normal = (await conn.execute(text(
                    "SELECT messages FROM chat_history WHERE title LIKE '%normal%'"
                ))).scalars().all()
                self.assertEqual(normal, [[{'content': f'{marker}_chat_0'}]])
                # An intentionally bypassed application WHERE still cannot see lab B.
                rows = (await conn.execute(text(
                    "SELECT messages FROM chat_history WHERE title LIKE '%missing%' OR TRUE --%'"
                ))).scalars().all()
                self.assertEqual(rows, normal)
                # Escaping history into own logs is an intended educational discovery.
                stolen = (await conn.execute(text(
                    "SELECT messages::text FROM chat_history WHERE FALSE "
                    "UNION ALL SELECT payload::text FROM audit_logs"
                ))).scalars().all()
                self.assertEqual(len(stolen), 1)
                self.assertIn(f'{marker}_audit_0', stolen[0])
                self.assertNotIn(f'{marker}_audit_1', stolen[0])

            denied = [
                'SELECT password_hash FROM public.users',
                'SELECT system_prompt FROM public.game_sessions',
                'SELECT * FROM public.security_audit_logs',
                'SELECT * FROM public.chat_threads',
                f'SELECT * FROM "{second.schema}".chat_history',
                f'SELECT * FROM "{second.schema}".audit_logs',
                'SELECT rolpassword FROM pg_catalog.pg_authid',
                "SELECT pg_read_file('/etc/passwd')",
                f'SET ROLE "{first.owner}"',
                f'SET ROLE "{second.reader}"',
                'SET ROLE ' + '"' + url.username.replace('"', '""') + '"',
                'SET SESSION AUTHORIZATION ' + '"' + url.username.replace('"', '""') + '"',
                'UPDATE audit_logs SET event_type=\'tampered\'',
                'DELETE FROM chat_history',
                'UPDATE public.game_sessions SET defense_budget=999',
                f'ALTER VIEW "{first.schema}".chat_history SET (security_barrier=false)',
                f'CREATE TABLE "{first.schema}".unauthorized (id int)',
                f'DROP VIEW "{first.schema}".audit_logs',
            ]
            for sql in denied:
                with self.subTest(operation=sql.split()[0], check=denied.index(sql)):
                    async with reader.connect() as conn:
                        # Disabling the optional read-only default must not bypass ACLs.
                        await conn.execute(text('SET TRANSACTION READ WRITE'))
                        with self.assertRaises(DBAPIError):
                            await conn.execute(text(sql))
                        await conn.rollback()
                        self.assertEqual(await conn.scalar(text('SELECT count(*) FROM chat_history')), 1)

            # A syntax error rolls back on close, and the same pool works afterwards.
            async with reader.connect() as conn:
                with self.assertRaises(DBAPIError):
                    await conn.execute(text("SELECT * FROM chat_history WHERE title LIKE '%'broken'%'") )
            async with reader.connect() as conn:
                self.assertEqual(await conn.scalar(text('SELECT count(*) FROM audit_logs')), 1)

            # Views are live; no ETL or copy refresh is needed.
            async with admin.begin() as conn:
                await conn.execute(text("""
                    INSERT INTO public.security_audit_logs (id,lab_instance_id,event_type,payload)
                    VALUES (:id,:lab,'SQLI_TEST_LIVE','{}'::jsonb)
                """), {'id': uuid4(), 'lab': labs[0]})
            async with reader.connect() as conn:
                self.assertEqual(await conn.scalar(text('SELECT count(*) FROM audit_logs')), 2)

            # Full mounted HTTP route, actual auth/repositories/SQL/SIEM. There
            # are no service mocks, dependency overrides, external HTTP or lifespan.
            import httpx
            from unittest.mock import patch
            from app.core.security import create_access_token
            from app.main import app, red_app
            self.assertFalse(red_app.dependency_overrides)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as mapping:
                json.dump({str(obj.lab_id): url.set(username=obj.reader, password=password).render_as_string(
                    hide_password=False) for obj in objects}, mapping)
                mapping.flush()
                with patch.object(settings, 'SQLI_CONNECTIONS_FILE', mapping.name):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url='http://integration') as client:
                        tokens = [create_access_token(identity) for identity in identities]

                        async def request(query, session=sessions[0], token=tokens[0]):
                            return await client.get('/api/red/chat/history',
                                params={'q': query, 'session_id': session},
                                headers={'Authorization': 'Bearer ' + token})

                        normal = await request('normal')
                        self.assertEqual(normal.status_code, 200)
                        self.assertEqual(len(normal.json()['results']), 1)
                        self.assertEqual(normal.json()['results'][0]['messages'],
                                         [{'content': f'{marker}_chat_0'}])
                        injected = await request("' OR TRUE --")
                        self.assertEqual(injected.status_code, 200)
                        self.assertEqual(injected.json(), normal.json())
                        own_logs = await request("' AND FALSE UNION ALL SELECT id,event_type,payload "
                                                 'FROM audit_logs --')
                        self.assertEqual(own_logs.status_code, 200)
                        self.assertTrue(any(row['messages'].get('message') == f'{marker}_audit_0'
                                            for row in own_logs.json()['results']))
                        self.assertNotIn(f'{marker}_audit_1', own_logs.text)

                        for source, columns in (
                            (f'"{second.schema}".chat_history', 'id,title,messages'),
                            ('public.users', 'id,username,to_jsonb(password_hash)'),
                            ('public.game_sessions', 'id,system_prompt,to_jsonb(defense_budget)'),
                        ):
                            denied_response = await request(
                                f"' AND FALSE UNION ALL SELECT {columns} FROM {source} --")
                            self.assertEqual(denied_response.status_code, 400)
                            self.assertIn('42501', denied_response.json()['detail'])

                        async with admin.connect() as conn:
                            before = await conn.scalar(text('SELECT count(*) FROM public.security_audit_logs '
                                                           'WHERE lab_instance_id=:lab'), {'lab': labs[0]})
                        foreign = await request("' OR TRUE --", session=sessions[1])
                        self.assertEqual(foreign.status_code, 404)
                        async with admin.connect() as conn:
                            self.assertEqual(await conn.scalar(text(
                                'SELECT count(*) FROM public.security_audit_logs WHERE lab_instance_id=:lab'
                            ), {'lab': labs[0]}), before, 'Foreign session reached query logging')

                        failed = await request("'")
                        self.assertEqual(failed.status_code, 400)
                        self.assertIn('42601', failed.json()['detail'])
                        recovered = await request('normal')
                        self.assertEqual(recovered.status_code, 200)
                        other = await request('normal', session=sessions[1], token=tokens[1])
                        self.assertEqual(other.status_code, 200)
                        self.assertEqual(other.json()['results'][0]['messages'],
                                         [{'content': f'{marker}_chat_1'}])
                        for response in (normal, injected, own_logs, failed, recovered, other):
                            for secret in (password, TEST_URL, url.password):
                                if secret:
                                    self.assertTrue(secret not in response.text, 'HTTP response exposed a secret')

                    async with admin.connect() as conn:
                        logs = (await conn.execute(text(
                            'SELECT event_type,payload FROM public.security_audit_logs '
                            'WHERE lab_instance_id=:lab'
                        ), {'lab': labs[0]})).mappings().all()
                        self.assertTrue(any(row['event_type'] == 'DB_QUERY' for row in logs))
                        self.assertTrue(any(row['event_type'] == 'SQL_ERROR' and
                            row['payload']['error'] == 'SQL syntax error [42601]' for row in logs))
                        serialized = json.dumps([dict(row) for row in logs], default=str)
                        for secret in (password, TEST_URL, url.password):
                            if secret:
                                self.assertTrue(secret not in serialized, 'SIEM payload exposed a secret')

            # Existing roles/views are never taken over or silently repaired.
            async with admin.begin() as conn:
                with self.assertRaises(IsolationError):
                    await provision_lab(conn, labs[0], password)

            # Ambient PUBLIC privileges must be detected. Each temporary grant is
            # rolled back and is never visible to other connections.
            class RollbackProbe(Exception):
                pass

            for grant in (
                'GRANT SELECT ON public.users TO PUBLIC',
                'GRANT SELECT (password_hash) ON public.users TO PUBLIC',
                'GRANT CREATE ON SCHEMA public TO PUBLIC',
            ):
                with self.assertRaises(RollbackProbe):
                    async with admin.begin() as conn:
                        await conn.exec_driver_sql(grant)
                        with self.assertRaises(IsolationError):
                            await check_reader_boundary(conn, first)
                        raise RollbackProbe()

            # A failed first provisioning rolls back BOTH roles and schema.
            failed_lab = uuid4()
            failed = LabObjects(failed_lab)
            with self.assertRaises(RollbackProbe):
                async with admin.begin() as conn:
                    # Use an existing fixture session; keep all changes uncommitted.
                    await conn.execute(text("UPDATE public.game_sessions SET lab_instance_id=:new "
                                            "WHERE lab_instance_id=:old"),
                                       {'new': failed_lab, 'old': labs[0]})
                    await provision_lab(conn, failed_lab, password)
                    raise RollbackProbe()
            async with admin.connect() as conn:
                self.assertFalse(await conn.scalar(text(
                    'SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)'
                ), {'role': failed.reader}))
                self.assertFalse(await conn.scalar(text(
                    'SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema)'
                ), {'schema': failed.schema}))
            print(f'PostgreSQL SQLi isolation verified; retained synthetic labs: {labs[0]}, {labs[1]}')
        except DBAPIError as exc:
            self.fail('PostgreSQL check failed; SQLSTATE ' + str(getattr(exc.orig, 'sqlstate', 'unknown'))
                      + '. SQL/parameters withheld to protect credentials.')
        finally:
            for engine in readers:
                await engine.dispose()
            await admin.dispose()
            await app_engine.dispose()


if __name__ == '__main__':
    unittest.main()
