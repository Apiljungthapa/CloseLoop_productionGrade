"""Cheap text pre-check for generated SQL.

Only the shape of the text is checked here. Keyword filtering on raw text rejects
harmless string values (e.g. ILIKE '%create%') and is bypassable, so every real
safety decision is made on the parsed syntax tree in ``db.sql_planner`` and, last,
by the PostgreSQL read-only transaction and database role.
"""
import re


class UnsafeSQL(ValueError):
    pass


def validate_sql(sql: str) -> str:
    """Return the statement without a trailing semicolon, or raise UnsafeSQL."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise UnsafeSQL("Empty SQL.")
    if not re.match(r"^(select|with)\b", text, re.IGNORECASE):
        raise UnsafeSQL("Only SELECT queries are allowed.")
    return text
