"""Explicit administrative preparation of a lab's read-only SQLi views."""
import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import sys
from uuid import UUID


async def run(lab_id, password):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.config import settings
    from app.db.sqli_isolation import IsolationError, provision_lab

    url = make_url(settings.DATABASE_URL)
    if url.drivername != 'postgresql+asyncpg':
        raise IsolationError('DATABASE_URL must use postgresql+asyncpg.')
    engine = create_async_engine(url, hide_parameters=True,
                                 connect_args={'timeout': 10})
    try:
        async with engine.begin() as conn:
            objects = await provision_lab(conn, lab_id, password)
        return {'lab_id': str(objects.lab_id), 'schema': objects.schema,
                'reader': objects.reader, 'owner': objects.owner}
    finally:
        await engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab-id', type=UUID, required=True)
    args = parser.parse_args(argv)
    try:
        password = os.environ.get('SQLI_LAB_PASSWORD')
        if password is None and sys.stdin.isatty():
            password = getpass.getpass('New SQLi reader password (24+ characters): ')
        result = asyncio.run(run(args.lab_id, password))
    except (KeyboardInterrupt, EOFError):
        print('provision_sqli: canceled; provisioning not confirmed.', file=sys.stderr)
        return 1
    except Exception:
        # DDL exceptions may contain the literal password in their SQL text.
        print('provision_sqli: failed; no success confirmed. Check database permissions, '
              'existing SQLi objects, lab setup and PUBLIC grants. Details withheld '
              'to protect credentials.', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
