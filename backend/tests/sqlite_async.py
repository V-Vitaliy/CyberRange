"""Run the application's awaited SQL against an in-memory SQLite session (not PostgreSQL)."""


class AsyncDatabase:
    """Run the application's awaited repository SQL against an in-memory database."""

    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    def add(self, row):
        self.session.add(row)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()
