from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .settings import env

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            env("DATABASE_URL"),
            min_size=0,
            max_size=5,
            open=True,
            kwargs={"row_factory": dict_row, "prepare_threshold": None, "autocommit": True},
        )
    return _pool
