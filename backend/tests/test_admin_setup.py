"""Service-free tests: python3 -m unittest discover -s backend/tests -p test_admin_setup.py.

Storage and transaction doubles exercise the seed without importing backend
dependencies, opening sockets, creating .env or running any services.
"""
import copy
import io
import json
import os
import unittest
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from scripts import admin_setup as setup


class Store:
    def __init__(self):
        self.users = []
        self.games = []
        self.results = [{"investigated_at": "kept", "flag_id": 7}]
        self.failure = None

    async def user(self, name):
        return next((u for u in self.users if u.username == name), None)

    async def sessions(self, lab):
        return [s for s in self.games if s.lab_instance_id == lab]

    def add_user(self, **values):
        row = SimpleNamespace(**values)
        self.users.append(row)
        return row

    def add_session(self, **values):
        row = SimpleNamespace(**values)
        self.games.append(row)
        return row

    async def flush(self):
        if self.failure:
            raise self.failure


class Engine:
    """Transaction double: snapshots rows, including rollback on failed commit."""
    def __init__(self, store):
        self.store = store
        self.commit_error = None
        self.connection_error = None
        self.ddl_error = None
        self.committed = False
        self.rolled_back = False
        self.commands = []

    @asynccontextmanager
    async def begin(self):
        if self.connection_error:
            raise self.connection_error
        state = copy.deepcopy(self.store.__dict__)
        try:
            yield self
            if self.commit_error:
                raise self.commit_error
        except BaseException:
            self.store.__dict__.update(state)
            self.rolled_back = True
            raise
        else:
            self.committed = True

    async def execute(self, query):
        self.commands.append(query)

    async def run_sync(self, fn):
        if self.ddl_error:
            raise self.ddl_error
        fn(self)


class SeedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store()
        self.options = setup.Options(lab_id=uuid4())
        self.engine = Engine(self.store)
        self.passwords = Mock(side_effect=lambda role: f" {role} password ")
        self.hasher = Mock(side_effect=lambda value: "test-hash:" + value)
        # Any accidental network use fails this suite immediately.
        self.network = patch("socket.socket.connect", side_effect=AssertionError("No network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    async def run_seed(self, options=None):
        @asynccontextmanager
        async def session(**kwargs):
            self.assertIs(kwargs["bind"], self.engine)
            yield self.store
        return await setup.seed_transaction(
            self.engine, session, SimpleNamespace(create_all=Mock()), str,
            lambda db: db, options or self.options, self.passwords, self.hasher)

    async def test_clean_seed_has_explicit_ids_roles_and_hashed_passwords(self):
        report = await self.run_seed()
        red, blue = self.store.users
        game = self.store.games[0]
        self.assertIsNotNone(red.id)
        self.assertEqual(game.team_id, red.id)
        self.assertEqual([red.role, blue.role], ["red_team", "blue_team"])
        self.assertTrue(all(row.lab_instance_id == self.options.lab_id
                            for row in (red, blue, game)))
        self.assertEqual(game.defense_budget, 5)
        self.assertTrue(game.is_solo)
        self.assertEqual(report["session_id"], str(game.id))
        self.assertTrue(all(report["created"].values()))
        self.assertEqual(red.password_hash, "test-hash: red_team password ")
        self.assertNotIn("password", json.dumps(report))
        self.assertNotIn("test-hash", json.dumps(report))
        self.assertTrue(self.engine.committed)
        self.assertIn("SELECT pg_advisory_xact_lock(721034, 1)", self.engine.commands)

    async def test_repeat_preserves_entire_existing_state_without_passwords(self):
        first = await self.run_seed()
        game = self.store.games[0]
        game.defense_budget = 2
        game.is_solo = False
        game.jwt_filter_enabled = True
        game.system_prompt = "Player choice"
        self.store.users[0].password_hash = "changed-hash"
        before = copy.deepcopy(self.store.__dict__)
        self.passwords.side_effect = AssertionError("Must not request an existing password")
        self.hasher.side_effect = AssertionError("Must not rehash")
        second = await self.run_seed(replace(self.options, budget=99, is_solo=True))
        self.assertEqual(self.store.__dict__, before)
        for key in ("lab_instance_id", "session_id", "team_id", "red_user_id", "blue_user_id"):
            self.assertEqual(first[key], second[key])
        self.assertFalse(any(second["created"].values()))
        self.assertFalse(second["is_solo"])

    async def test_explicit_team_and_missing_user_keep_existing_session(self):
        team = uuid4()
        first = await self.run_seed(replace(self.options, team_id=team, is_solo=False))
        self.store.users.pop()  # Partial lab with missing blue user.
        old_session = copy.deepcopy(self.store.games)
        self.passwords.reset_mock()
        second = await self.run_seed()
        self.passwords.assert_called_once_with("blue_team")
        self.assertEqual(self.store.games, old_session)
        self.assertEqual(second["team_id"], str(team))
        self.assertEqual(second["session_id"], first["session_id"])

    async def test_conflicting_role_lab_team_and_multiple_sessions_do_not_write(self):
        await self.run_seed()
        for kind in ("role", "lab", "team", "multiple"):
            with self.subTest(kind=kind):
                saved = copy.deepcopy(self.store.__dict__)
                options = self.options
                if kind == "role":
                    self.store.users[0].role = "admin"
                elif kind == "lab":
                    options = replace(options, lab_id=uuid4())
                elif kind == "team":
                    options = replace(options, team_id=uuid4())
                else:
                    self.store.games.append(copy.deepcopy(self.store.games[0]))
                before = copy.deepcopy(self.store.__dict__)
                with self.assertRaises(setup.SetupError):
                    await self.run_seed(options)
                self.assertEqual(self.store.__dict__, before)
                self.store.__dict__.update(saved)

    async def test_missing_second_password_rolls_back(self):
        self.passwords.side_effect = ["red-secret", ""]
        with self.assertRaises(setup.SetupError):
            await self.run_seed()
        self.assertEqual(self.store.users, [])
        self.assertEqual(self.store.games, [])
        self.assertTrue(self.engine.rolled_back)

    async def test_flush_commit_and_ddl_failures_do_not_leave_seed_rows(self):
        for stage in ("flush", "commit", "ddl"):
            with self.subTest(stage=stage):
                error = RuntimeError("private SQL parameters")
                self.store.failure = error if stage == "flush" else None
                self.engine.commit_error = error if stage == "commit" else None
                self.engine.ddl_error = error if stage == "ddl" else None
                with self.assertRaises(RuntimeError):
                    await self.run_seed()
                self.assertEqual(self.store.users, [])
                self.assertEqual(self.store.games, [])
                self.assertTrue(self.engine.rolled_back)


class CliTests(unittest.TestCase):
    def test_password_whitespace_preserved_and_empty_password_rejected(self):
        with patch.dict(os.environ, {"RED_TEAM_PASSWORD": "  secret  "}):
            self.assertEqual(setup.read_password("red_team"), "  secret  ")
        with patch.dict(os.environ, {"RED_TEAM_PASSWORD": ""}):
            with self.assertRaises(setup.SetupError):
                setup.read_password("red_team")

    def test_missing_password_noninteractive_and_hidden_input(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(setup.SetupError):
                setup.read_password("blue_team")
        with patch.dict(os.environ, {}, clear=True), patch("sys.stdin.isatty", return_value=True), \
                patch.object(setup.getpass, "getpass", return_value=" hidden "):
            self.assertEqual(setup.read_password("blue_team"), " hidden ")

    def test_validation(self):
        options = setup.Options(uuid4())
        for invalid in (replace(options, red_username=""), replace(options, budget=-1),
                        replace(options, blue_username=options.red_username),
                        replace(options, lab_id="bad"), replace(options, team_id="bad")):
            with self.subTest(invalid=invalid), self.assertRaises(setup.SetupError):
                invalid.validate()

    def test_cli_errors_hide_secrets_and_return_nonzero(self):
        for error in (ConnectionRefusedError("secret-dsn"), TimeoutError("secret-dsn"),
                      ValueError("secret-dsn"), RuntimeError("secret-dsn"),
                      ImportError("secret-dsn")):
            output = io.StringIO()
            with patch.object(setup, "run_setup", AsyncMock(side_effect=error)), \
                    redirect_stderr(output), redirect_stdout(output):
                code = setup.main(["--lab-id", str(uuid4())])
            self.assertEqual(code, 1)
            self.assertIn("admin_setup:", output.getvalue())
            self.assertNotIn("secret-dsn", output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())

    def test_cli_prints_json_only_after_success(self):
        report = {"session_id": str(uuid4())}
        output = io.StringIO()
        with patch.object(setup, "run_setup", AsyncMock(return_value=report)), redirect_stdout(output):
            self.assertEqual(setup.main(["--lab-id", str(uuid4())]), 0)
        self.assertEqual(json.loads(output.getvalue()), report)


if __name__ == "__main__":
    unittest.main()
