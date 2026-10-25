"""Manual SQL console using ONLY a prepared demo's restricted reader login."""
import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


async def console(label):
    path = Path(__file__).resolve().parents[1] / '.local-sqli59' / 'connections.json'
    manifest = json.loads(path.read_text())
    if manifest['status'] != 'committed':
        raise ValueError('Demo commit is unconfirmed; inspect preparation before use.')
    lab = manifest['labs'][label]
    other = manifest['labs']['B' if label == 'A' else 'A']
    engine = create_async_engine(lab['url'], hide_parameters=True,
                                 connect_args={'timeout': 10})
    try:
        async with engine.connect() as conn:
            identity = (await conn.exec_driver_sql('SELECT session_user, current_database()')).one()
            if identity[0] != lab['reader'] or identity[1] != manifest['database']:
                raise ValueError('Reader identity mismatch.')
            await conn.rollback()
            print(f'Connected as {identity[0]} to {identity[1]}; lab {label}.')
            print(f"Own schema: {lab['schema']}; other schema: {other['schema']}")
            print(f"Own session: {lab['session_id']}")
            print('SQL ends with ;. Errors automatically ROLLBACK. COMMIT/ROLLBACK supported. Quit: \\q')
            statement = []
            while True:
                try:
                    line = input('sql> ' if not statement else '...> ')
                except EOFError:
                    break
                if line.strip() == '\\q':
                    break
                statement.append(line)
                if not line.rstrip().endswith(';'):
                    continue
                sql = '\n'.join(statement).strip()
                statement.clear()
                try:
                    if sql.rstrip(';').strip().upper() == 'ROLLBACK':
                        await conn.rollback()
                        print('ROLLBACK')
                    elif sql.rstrip(';').strip().upper() == 'COMMIT':
                        await conn.commit()
                        print('COMMIT')
                    else:
                        result = await conn.exec_driver_sql(sql)
                        if result.returns_rows:
                            rows = result.mappings().fetchmany(101)
                            for row in rows[:100]:
                                print(json.dumps(dict(row), ensure_ascii=False, default=str))
                            print(f'{min(len(rows), 100)} row(s)' + ('; output truncated' if len(rows) > 100 else ''))
                        else:
                            print(f'OK; affected rows: {result.rowcount}')
                except DBAPIError as exc:
                    await conn.rollback()
                    print(f'SQL error [{getattr(exc.orig, "sqlstate", "unknown")}]: {exc.orig}')
                    print('ROLLBACK complete; next statement can run.')
                except Exception:
                    await conn.rollback()
                    print('Statement failed; ROLLBACK complete.')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lab', choices=('A', 'B'), nargs='?', default='A')
    args = parser.parse_args()
    try:
        asyncio.run(console(args.lab))
    except KeyboardInterrupt:
        pass
    except Exception:
        parser.exit(1, 'Console could not connect; inspect local demo preparation. Credentials withheld.\n')
