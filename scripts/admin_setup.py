"""Explicit seed for educational CyberRange; importing never connects to a DB."""
import argparse
import asyncio
import getpass
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


class SetupError(Exception):
    """A deliberately secret-free CLI message."""


@dataclass(frozen=True)
class Options:
    lab_id: UUID
    red_username: str = "red_student"
    blue_username: str = "blue_analyst"
    team_id: UUID | None = None
    budget: int = 5
    is_solo: bool = True

    def validate(self):
        if not isinstance(self.lab_id, UUID) or (
            self.team_id is not None and not isinstance(self.team_id, UUID)
        ):
            raise SetupError("lab_id и team_id должны быть UUID.")
        if any(not name or name != name.strip()
               for name in (self.red_username, self.blue_username)):
            raise SetupError("Имена не могут быть пустыми или с пробелами по краям.")
        if self.red_username == self.blue_username:
            raise SetupError("Имена red/blue должны различаться.")
        if self.budget < 0:
            raise SetupError("Начальный бюджет не может быть отрицательным.")


def read_password(role):
    key = f"{role.upper()}_PASSWORD"
    password = os.environ.get(key)
    if password is None and sys.stdin.isatty():
        password = getpass.getpass(f"Пароль {role}: ")
    if not password:
        raise SetupError(f"Задайте {key} в окружении или используйте скрытый ввод.")
    return password  # Preserve whitespace exactly, as the login does.


async def seed(store, options, password_lookup, hash_password):
    """Provision rows inside a caller-owned transaction; preserve existing rows."""
    options.validate()
    users = []
    roles = ((options.red_username, "red_team"), (options.blue_username, "blue_team"))
    for name, role in roles:
        user = await store.user(name)
        if user is not None and (user.role != role or user.lab_instance_id != options.lab_id):
            raise SetupError("Имя уже занято пользователем другой роли или лаборатории.")
        users.append(user)
    sessions = await store.sessions(options.lab_id)
    if len(sessions) > 1:
        raise SetupError("У лаборатории несколько сессий; устраните неоднозначность вручную.")
    session = sessions[0] if sessions else None
    if session is not None and options.team_id is not None and session.team_id != options.team_id:
        raise SetupError("--team-id конфликтует с существующей сессией.")
    # Resolve and hash both secrets before writing any rows.
    hashes = {}
    for user, (_, role) in zip(users, roles):
        if user is None:
            password = password_lookup(role)
            if not password:
                raise SetupError("Пароль нового пользователя не может быть пустым.")
            hashes[role] = hash_password(password)
    created = {"red_team": users[0] is None, "blue_team": users[1] is None,
               "session": session is None}
    for index, (name, role) in enumerate(roles):
        if users[index] is None:
            users[index] = store.add_user(
                id=uuid4(), username=name, role=role,
                lab_instance_id=options.lab_id, password_hash=hashes[role])
    if session is None:
        session = store.add_session(
            id=uuid4(), lab_instance_id=options.lab_id,
            team_id=options.team_id or users[0].id,
            is_solo=options.is_solo, defense_budget=options.budget)
    await store.flush()
    return {
        "lab_instance_id": str(options.lab_id), "session_id": str(session.id),
        "team_id": str(session.team_id), "is_solo": session.is_solo,
        "red_user_id": str(users[0].id), "blue_user_id": str(users[1].id),
        "red_username": users[0].username, "blue_username": users[1].username,
        "created": created,
    }


class SqlStore:
    def __init__(self, db, user_model, session_model, select):
        self.db, self.User, self.Session, self.select = db, user_model, session_model, select

    async def user(self, name):
        result = await self.db.execute(self.select(self.User).where(self.User.username == name))
        return result.scalars().one_or_none()

    async def sessions(self, lab_id):
        result = await self.db.execute(
            self.select(self.Session).where(self.Session.lab_instance_id == lab_id))
        return result.scalars().all()

    def add_user(self, **values):
        row = self.User(**values)
        self.db.add(row)
        return row

    def add_session(self, **values):
        row = self.Session(**values)
        self.db.add(row)
        return row

    async def flush(self):
        await self.db.flush()


async def seed_transaction(engine, session_factory, metadata, text, make_store,
                           options, password_lookup, hash_password):
    # Serialize this script's create_all and username checks across all labs.
    # This lock does not govern normal application requests.
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        await connection.execute(text("SELECT pg_advisory_xact_lock(721034, 1)"))
        await connection.run_sync(metadata.create_all)
        async with session_factory(bind=connection, expire_on_commit=False) as db:
            result = await seed(make_store(db), options, password_lookup, hash_password)
    return result  # Success only after commit of both DDL and seed rows.


async def run_setup(options):
    options.validate()
    backend = str(Path(__file__).resolve().parents[1] / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    # Lazy imports keep --help and unit tests independent of backend deps.
    from sqlalchemy import select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from app.core.config import settings
    from app.core.security import get_password_hash
    from app.db.database import Base
    from app.db.models import User, GameSession

    url = make_url(settings.DATABASE_URL)
    if url.drivername != "postgresql+asyncpg":
        raise SetupError("DATABASE_URL должен использовать postgresql+asyncpg.")
    engine = create_async_engine(url, echo=False, hide_parameters=True,
                                 connect_args={"timeout": 10})
    try:
        return await seed_transaction(
            engine, AsyncSession, Base.metadata, text,
            lambda db: SqlStore(db, User, GameSession, select),
            options, read_password, get_password_hash)
    finally:
        await engine.dispose()


def parser():
    result = argparse.ArgumentParser(description="Подготовка одной учебной лаборатории CyberRange.")
    result.add_argument("--lab-id", required=True, type=UUID,
                        help="Стабильный UUID лаборатории; повторно используйте тот же UUID.")
    result.add_argument("--team-id", type=UUID,
                        help="При создании: UUID команды, по умолчанию ID red-пользователя.")
    result.add_argument("--red-username", default="red_student")
    result.add_argument("--blue-username", default="blue_analyst")
    result.add_argument("--budget", type=int, default=5, help="Только для новой сессии.")
    result.add_argument("--is-solo", action=argparse.BooleanOptionalAction, default=True,
                        help="Auto-SOC для новой сессии; существующая настройка сохраняется.")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run_setup(Options(**vars(args))))
    except SetupError as exc:
        print(f"admin_setup: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("admin_setup: ввод отменён; подготовка не завершена.", file=sys.stderr)
        return 1
    except ImportError:
        print("admin_setup: установите backend/requirements-groq.txt в Python-окружение.", file=sys.stderr)
        return 1
    except Exception:
        # DB exceptions can contain SQL parameters, hashes and credentials.
        print("admin_setup: подготовка не подтверждена. Проверьте DATABASE_URL, доступность "
              "PostgreSQL, права CONNECT/USAGE/CREATE и записи в таблицы, совместимость "
              "схемы. После восстановления повторите ту же команду; секреты скрыты.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
