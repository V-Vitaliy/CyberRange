"""Intentional educational SQLi, executed ONLY through a restricted lab login."""
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import ROOT_DIR, settings
from app.db.sqli_isolation import IsolationError, LabObjects, check_reader_boundary


class HistoryQueryError(Exception):
    """SQLSTATE-based message safe for HTTP/SIEM; no connection secrets or SQL dump."""


def reader_url(lab_id):
    objects = LabObjects(lab_id)
    try:
        if not settings.SQLI_CONNECTIONS_FILE:
            raise ValueError()
        path = Path(settings.SQLI_CONNECTIONS_FILE)
        if not path.is_absolute():
            path = ROOT_DIR / path
        data = json.loads(path.read_text())
        url = make_url(data[str(objects.lab_id)])
        control = make_url(settings.DATABASE_URL)
        if (url.drivername != 'postgresql+asyncpg' or url.username != objects.reader
                or not url.password or url.database != control.database):
            raise ValueError()
        # Views/privileges were provisioned in the same PostgreSQL deployment.
        def host(value):
            return 'localhost' if value in ('localhost', '127.0.0.1', '::1') else value
        if host(url.host) != host(control.host) or (url.port or 5432) != (control.port or 5432):
            raise ValueError()
        return url
    except Exception:
        raise IsolationError('History search needs a configured restricted connection for this lab.') from None


async def search_history(lab_id, session_id, query):
    objects = LabObjects(lab_id)
    session_id = UUID(str(session_id))
    url = reader_url(objects.lab_id)
    # UUID/schema come from validated server context. query is deliberately NOT
    # escaped or parameterized: exploitation inside the protected views is intended.
    raw_query = (
        f'SELECT id, title, messages FROM "{objects.schema}".chat_history '
        f"WHERE session_id = '{session_id}'::uuid "
        f"AND (title || ' ' || messages::text) ILIKE '%{query}%' "
        'ORDER BY created_at'
    )
    engine = create_async_engine(
        url, hide_parameters=True, poolclass=NullPool,
        connect_args={'timeout': 10, 'command_timeout': 5,
                      'server_settings': {'statement_timeout': '3000', 'lock_timeout': '1000',
                                          'default_transaction_read_only': 'on'}},
    )
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                identity = (await conn.execute(text('SELECT session_user, current_user'))).one()
                if tuple(identity) != (objects.reader, objects.reader):
                    raise IsolationError('History connection is not the expected lab reader.')
                await check_reader_boundary(conn, objects)
                # Use driver SQL: SQLAlchemy must not interpret colon-containing
                # student input as bind parameters. asyncpg accepts one statement.
                result = await conn.exec_driver_sql(raw_query)
                return [dict(row) for row in result.mappings().all()], raw_query
    except IsolationError:
        raise
    except DBAPIError as exc:
        code = getattr(exc.orig, 'sqlstate', None)
        messages = {
            '42601': 'SQL syntax error', '42501': 'SQL permission denied',
            '42P01': 'SQL relation does not exist', '42703': 'SQL column does not exist',
            '42804': 'SQL type mismatch', '22P02': 'SQL invalid input value',
            '57014': 'SQL query timed out',
        }
        if code in messages:
            raise HistoryQueryError(f'{messages[code]} [{code}]') from None
        # Do not include driver exception strings: they can contain connection
        # details, SQL and data values from PostgreSQL errors.
        raise HistoryQueryError('History database query failed.') from None
    except Exception:
        raise HistoryQueryError('History database is unavailable.') from None
    finally:
        await engine.dispose()
