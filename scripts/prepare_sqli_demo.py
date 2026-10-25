"""Prepare retained synthetic labs for MANUAL SQLi boundary verification.

Targets only the explicitly approved local cyberrange_setup_test database.
Does not run attack checks, reset data, or modify existing labs.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.local-sqli59' / 'connections.json'


async def prepare():
    sys.path.insert(0, str(ROOT / 'backend'))
    from sqlalchemy import event, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from app.core.config import settings
    from app.db.database import Base
    from app.db.models import ChatThread, GameSession, SecurityAuditLog, User
    from app.db.sqli_isolation import provision_lab

    url = make_url(settings.DATABASE_URL).set(database='cyberrange_setup_test')
    if url.drivername != 'postgresql+asyncpg' or url.host not in ('localhost', '127.0.0.1'):
        raise ValueError('This demo requires the approved localhost test PostgreSQL.')
    STATE.parent.mkdir(mode=0o700, exist_ok=True)
    if STATE.parent.is_symlink():
        raise ValueError('Private state directory must not be a symlink.')
    os.chmod(STATE.parent, 0o700)
    # Preserve credentials even if the process is interrupted after DB commit.
    # Exclusive creation prevents replacing a previously prepared demo.
    state_fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    manifest = {'status': 'not_committed', 'database': url.database, 'labs': {}}
    engine = create_async_engine(url, hide_parameters=True, connect_args={'timeout': 10})

    @event.listens_for(engine.sync_engine, 'handle_error')
    def error_stage(context):
        # No SQL, parameters or server messages: they can include credentials.
        statement = (context.statement or '').strip().split()
        command = statement[0].upper() if statement else 'CONNECT'
        code = getattr(context.original_exception, 'sqlstate', 'unknown')
        print(f'PostgreSQL rejected command {command}; SQLSTATE {code}.', file=sys.stderr)

    try:
        with os.fdopen(state_fd, 'w') as saved:
            async with engine.begin() as conn:
                if await conn.scalar(text('SELECT current_database()')) != 'cyberrange_setup_test':
                    raise ValueError('Database confirmation mismatch.')
                await conn.run_sync(Base.metadata.create_all)
                async with AsyncSession(bind=conn, expire_on_commit=False) as db:
                    for label in ('A', 'B'):
                        lab, session_id, user_id = uuid4(), uuid4(), uuid4()
                        password = secrets.token_urlsafe(36)
                        user = User(id=user_id, username=f'manual59_{label}_{lab.hex}',
                                    password_hash='synthetic-unusable-login', role='red_team',
                                    lab_instance_id=lab)
                        session = GameSession(id=session_id, team_id=user_id, lab_instance_id=lab,
                                              system_prompt=f'SYNTHETIC_CONTROL_PLANE_{label}')
                        db.add_all([user, session])
                        await db.flush()
                        for kind in ('Welcome', 'Internal'):
                            db.add(ChatThread(id=uuid4(), user_id=user_id, session_id=session_id,
                                              title=f'{kind} {label}', messages=[{
                                                  'role': 'user',
                                                  'content': f'LAB_{label}_{kind.upper()}_MESSAGE',
                                              }]))
                        db.add(SecurityAuditLog(lab_instance_id=lab, event_type='MANUAL_SQLI_DEMO',
                                                payload={'message': f'LAB_{label}_AUDIT_MARKER'}))
                        await db.flush()
                        objects = await provision_lab(conn, lab, password)
                        manifest['labs'][label] = {
                            'lab_id': str(lab), 'session_id': str(session_id),
                            'schema': objects.schema, 'reader': objects.reader, 'owner': objects.owner,
                            'url': url.set(username=objects.reader, password=password).render_as_string(
                                hide_password=False),
                        }
                json.dump(manifest, saved, indent=2)
                saved.flush()
                os.fsync(saved.fileno())
            # No success state is written until the outer DB transaction commits.
            manifest['status'] = 'committed'
            saved.seek(0)
            saved.truncate()
            json.dump(manifest, saved, indent=2)
            saved.flush()
            os.fsync(saved.fileno())
        for label, lab in manifest['labs'].items():
            print(f"Lab {label}: {lab['schema']} (session {lab['session_id']})")
        runtime_path = STATE.with_name('readers.json')
        runtime_fd = os.open(runtime_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(runtime_fd, 'w') as runtime:
            json.dump({lab['lab_id']: lab['url'] for lab in manifest['labs'].values()}, runtime, indent=2)
        print('Prepared two labs: 2 chat threads and 1 audit event per lab.')
        print('Private connections saved to .local-sqli59/connections.json (mode 0600).')
        print('Runtime reader map saved to .local-sqli59/readers.json (mode 0600).')
        print('Attack checks were not run. Existing records were not reset or deleted.')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    try:
        asyncio.run(prepare())
    except FileExistsError:
        print('Demo state already exists; not overwriting. Inspect it before retrying.', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # IsolationError messages contain no SQL/credentials; DB exceptions may.
        message = str(exc) if type(exc).__name__ == 'IsolationError' else type(exc).__name__
        print(f'Demo preparation failed: {message}. Do not delete state before inspection.', file=sys.stderr)
        sys.exit(1)
