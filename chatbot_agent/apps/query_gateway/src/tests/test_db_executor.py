import json

import pytest

from core.config import Settings
from db import executor
from db.sql_planner import SQLPlanningError


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    def fetchall(self):
        return self.rows


def test_execute_scopes_tenant_and_starts_read_only_transaction(monkeypatch):
    connection = FakeConnection([{"title": "Close books"}])
    monkeypatch.setattr(
        executor,
        "get_settings",
        lambda: Settings(
            DB_ENABLED=True,
            DB_URL="postgresql://test",
            DB_QUERY_TIMEOUT_MS=3210,
        ),
    )
    monkeypatch.setattr(executor, "get_db_connection", lambda _url: connection)
    monkeypatch.setattr(executor, "_load_schema", lambda _url: {"tasks": {"id", "tenant_id", "title"}})
    monkeypatch.setattr(executor, "_ensure_tenant_exists", lambda *_args: None)

    rows = executor.execute("SELECT title FROM tasks", tenant_id="tenant-1", max_rows=10)

    assert rows == [{"title": "Close books"}]
    assert connection.statements[0] == ("SET TRANSACTION READ ONLY", None)
    assert connection.statements[1] == (
        "SELECT set_config('statement_timeout', %s, true)",
        ("3210ms",),
    )
    assert connection.statements[2] == (
        "SELECT set_config('TimeZone', %s, true)",
        ("Asia/Kathmandu",),
    )
    query = connection.statements[3][0]
    assert "tasks.tenant_id = 'tenant-1'" in query
    assert query.endswith("LIMIT 10")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM tasks",
        "SELECT * FROM tasks",
        "SELECT id FROM private.tasks",
        "SELECT pg_sleep(60) FROM tasks",
        "SELECT id FROM tasks; SELECT id FROM users",
    ],
)
def test_execute_rejects_unsafe_sql_before_opening_execution_connection(monkeypatch, sql):
    monkeypatch.setattr(
        executor,
        "get_settings",
        lambda: Settings(DB_ENABLED=True, DB_URL="postgresql://test"),
    )
    monkeypatch.setattr(executor, "_load_schema", lambda _url: {"tasks": {"id", "tenant_id"}})
    monkeypatch.setattr(executor, "_ensure_tenant_exists", lambda *_args: None)
    monkeypatch.setattr(
        executor,
        "get_db_connection",
        lambda *_args: pytest.fail("Unsafe SQL must not reach the database connection."),
    )

    with pytest.raises(SQLPlanningError):
        executor.execute(sql, tenant_id="tenant-1")


def test_generate_answer_uses_original_question_and_serialized_rows():
    class FakeGemini:
        def generate(self, system_prompt, user_prompt, temperature):
            assert "untrusted" in system_prompt
            prompt = json.loads(user_prompt)
            assert prompt["original_question"] == "How many tasks?"
            assert prompt["rows"] == ['{"count": 3}']
            assert temperature == 0
            return "There are 3 tasks."

    answer, truncated = executor.generate_answer(
        "How many tasks?",
        [{"count": 3}],
        FakeGemini(),
    )

    assert answer == "There are 3 tasks."
    assert not truncated


def test_generate_answer_marks_omitted_result_rows():
    class FakeGemini:
        def generate(self, _system_prompt, user_prompt, temperature):
            payload = json.loads(user_prompt)
            assert payload["rows_truncated"]
            assert len(payload["rows"]) == 1
            assert temperature == 0
            return "The result is truncated."

    answer, truncated = executor.generate_answer(
        "List tasks",
        [{"value": "a" * 15000}, {"value": "b" * 15000}],
        FakeGemini(),
    )

    assert answer == "The result is truncated."
    assert truncated
