from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr


@asynccontextmanager
async def postgres_checkpointer(url: SecretStr) -> AsyncIterator[AsyncPostgresSaver]:
    """A saver keeping conversations in the Postgres at url, with its tables created,
    whose connections close on exit"""
    async with AsyncConnectionPool(
        url.get_secret_value(),
        check=AsyncConnectionPool.check_connection,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
