"""Rich, cached description of the tenant-owned tables the planner may query."""
import re
import time
from dataclasses import asdict, dataclass, field
from threading import Lock

from core import cache as cache_layer
from core.config import get_settings
from core.db_connector import get_db_connection
from db.domain_knowledge import TABLE_NOTES

SENSITIVE_NAME = re.compile(
    r"(password|secret|token|hash|credential|storage_path|failed_login|locked_until|client_id)",
    re.IGNORECASE,
)
DENIED_TABLES = {"audit_logs", "password_reset_tokens", "refresh_tokens"}
CACHE_TTL_S = 600


@dataclass
class ColumnInfo:
    name: str
    type: str
    enum_values: tuple[str, ...] = ()
    references: str | None = None
    primary_key: bool = False


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)


_COLUMNS_SQL = """
    SELECT c.table_name, c.column_name, c.data_type, c.udt_name,
           (SELECT array_agg(e.enumlabel ORDER BY e.enumsortorder)
              FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
             WHERE t.typname = c.udt_name) AS enum_values
    FROM information_schema.columns AS c
    WHERE c.table_schema = 'public'
      AND EXISTS (SELECT 1 FROM information_schema.columns AS tc
                   WHERE tc.table_schema = 'public' AND tc.table_name = c.table_name
                     AND tc.column_name = 'tenant_id')
    ORDER BY c.table_name, c.ordinal_position
"""
_KEYS_SQL = """
    SELECT con.contype, con.conrelid::regclass::text AS table_name, a.attname AS column_name,
           con.confrelid::regclass::text AS ref_table, ra.attname AS ref_column
    FROM pg_constraint AS con
    JOIN pg_attribute AS a ON a.attrelid = con.conrelid AND a.attnum = con.conkey[1]
    LEFT JOIN pg_attribute AS ra ON ra.attrelid = con.confrelid AND ra.attnum = con.confkey[1]
    WHERE con.contype IN ('p', 'f') AND con.connamespace = 'public'::regnamespace
      AND array_length(con.conkey, 1) = 1
"""

_cache: dict[str, tuple[float, dict[str, TableInfo]]] = {}
_cache_lock = Lock()


def _build(db_url: str) -> dict[str, TableInfo]:
    with get_db_connection(db_url) as connection:
        column_rows = connection.execute(_COLUMNS_SQL).fetchall()
        key_rows = connection.execute(_KEYS_SQL).fetchall()

    catalog: dict[str, TableInfo] = {}
    for row in column_rows:
        table, column = row["table_name"], row["column_name"]
        if table in DENIED_TABLES or SENSITIVE_NAME.search(table) or SENSITIVE_NAME.search(column):
            continue
        data_type = row["udt_name"] if row["data_type"] == "USER-DEFINED" else row["data_type"]
        catalog.setdefault(table, TableInfo(table)).columns.append(
            ColumnInfo(column, data_type, tuple(row["enum_values"] or ()))
        )

    for row in key_rows:
        info = catalog.get(row["table_name"].removeprefix("public."))
        if info is None:
            continue
        column = next((c for c in info.columns if c.name == row["column_name"]), None)
        if column is None:
            continue
        if row["contype"] == "p":
            column.primary_key = True
        else:
            ref_table = (row["ref_table"] or "").removeprefix("public.")
            if ref_table in catalog and not SENSITIVE_NAME.search(row["ref_column"] or ""):
                column.references = f"{ref_table}.{row['ref_column']}"
    return catalog


def _scrub(catalog: dict[str, TableInfo]) -> dict[str, TableInfo]:
    """Re-apply the security filters. Applied to anything that came back from Redis, so a tampered
    cache entry can never widen the allow-list of tables/columns the SQL validator trusts."""
    clean: dict[str, TableInfo] = {}
    for name, info in catalog.items():
        if name in DENIED_TABLES or SENSITIVE_NAME.search(name):
            continue
        columns = [c for c in info.columns if not SENSITIVE_NAME.search(c.name)]
        for column in columns:
            if column.references and (
                column.references.split(".")[0] in DENIED_TABLES or SENSITIVE_NAME.search(column.references)
            ):
                column.references = None
        clean[name] = TableInfo(name, columns)
    return clean


def load_catalog(db_url: str, *, force: bool = False) -> dict[str, TableInfo]:
    """Return the catalog, refreshed at most every CACHE_TTL_S seconds."""
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(db_url)
        if cached and not force and now - cached[0] < CACHE_TTL_S:
            return cached[1]
    shared = cache_layer.get_cache()
    key = shared.make_key("schema", db_url)  # the URL holds credentials: only its hash is used
    stored = None if force else shared.get_json("schema", key)
    if isinstance(stored, dict):
        try:
            catalog = {
                name: TableInfo(
                    name,
                    [ColumnInfo(**{**c, "enum_values": tuple(c.get("enum_values", ()))}) for c in table["columns"]],
                )
                for name, table in stored.items()
            }
            catalog = _scrub(catalog)
        except (TypeError, KeyError, AttributeError):
            catalog = None
    else:
        catalog = None
    if catalog is None:
        catalog = _build(db_url)
        shared.set_json(
            "schema", key, {name: asdict(info) for name, info in catalog.items()}, get_settings().CACHE_TTL_SCHEMA_S
        )
    with _cache_lock:
        _cache[db_url] = (now, catalog)
    return catalog


def schema_from_catalog(catalog: dict[str, TableInfo]) -> dict[str, set[str]]:
    return {name: {column.name for column in info.columns} for name, info in catalog.items()}


def render_catalog(catalog: dict[str, TableInfo]) -> str:
    """Compact text for the prompt. tenant_id is hidden: the server adds that filter itself."""
    blocks: list[str] = []
    for name, info in sorted(catalog.items()):
        note = TABLE_NOTES.get(name)
        lines = [f"public.{name}:" + (f" {note}" if note else "")]
        for column in info.columns:
            if column.name == "tenant_id":
                continue
            parts = [f"  {column.name} {column.type}"]
            if column.primary_key:
                parts.append("PK")
            if column.references:
                parts.append(f"-> {column.references}")
            if column.enum_values:
                parts.append("values: " + "|".join(column.enum_values))
            lines.append(" ".join(parts))
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def render_plain_schema(schema: dict[str, set[str]]) -> str:
    """Fallback when no rich catalog is available."""
    return "\n".join(
        f"public.{table}: {', '.join(sorted(columns))}" for table, columns in sorted(schema.items())
    )
