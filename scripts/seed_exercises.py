"""Seed exercise flags (EX-03, EX-04, EX-05) into one lab created by admin_setup.

Needs PostgreSQL, Redis, Chroma and the embedding model, like the backend itself.
Flag values are printed only when they are created; store them for grading.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID


async def run(lab_id: UUID) -> dict:
    backend = str(Path(__file__).resolve().parents[1] / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    # Lazy imports keep --help independent of backend deps.
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.redis_client import redis_client
    from app.db.database import Base, engine
    from app.documents.chroma_client import VectorStore
    from app.documents.service import ETLWorker
    from app.labs.exercises import private_pdf_indexer, seed_exercise_flags

    index = private_pdf_indexer(ETLWorker(VectorStore()), redis_client)
    try:
        async with engine.begin() as connection:
            # Creates lab_secrets on databases provisioned before LAB-6; existing tables are untouched.
            await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                created = await seed_exercise_flags(db, lab_id, index)
    finally:
        await engine.dispose()
    return {"lab_instance_id": str(lab_id), "created": created}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed exercise flags into one CyberRange lab.")
    parser.add_argument("--lab-id", required=True, type=UUID, help="The lab UUID used with admin_setup.")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args.lab_id))
    except ImportError:
        print("seed_exercises: install the backend requirements in the Python environment.", file=sys.stderr)
        return 1
    except Exception as exc:
        from app.labs.exercises import SeedError

        if isinstance(exc, SeedError):
            print(f"seed_exercises: {exc}", file=sys.stderr)
        else:
            # DB and service errors can contain connection strings; keep the message generic.
            print(
                "seed_exercises: seeding failed; the database was not changed. Check PostgreSQL, Redis, "
                "Chroma and the embedding model, then repeat the same command.",
                file=sys.stderr,
            )
        return 1
    if not result["created"]:
        print("seed_exercises: the lab already has all exercise flags; values are shown only once.", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
