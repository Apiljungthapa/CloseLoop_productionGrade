"""PostgreSQL connection helpers for the query gateway."""
from typing import Any

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings


def get_db_connection(
    db_url: str | None = None,
) -> psycopg.Connection[dict[str, Any]]:
    """Open a PostgreSQL connection using the configured URL or an explicit URL."""
    connection_url = get_settings().DB_URL if db_url is None else db_url
    connection_url = connection_url.strip()
    if not connection_url:
        raise RuntimeError("DB_URL is not configured.")

    return psycopg.connect(connection_url, row_factory=dict_row, connect_timeout=5)
