import pytest

from core.config import Settings
from core.exceptions import FeatureDisabled
import psycopg

from db import sql_planner
from db.domain_knowledge import EXAMPLES
from db.sql_planner import (
    ClarificationNeeded,
    SQLPlanningError,
    _parse_plan_response,
    _validate_and_scope,
    plan_sql,
    plan_sql_detailed,
)


SCHEMA = {
    "tasks": {"id", "tenant_id", "title", "status"},
    "users": {"id", "tenant_id", "name", "email"},
}


def _no_catalog(_db_url):
    raise RuntimeError("catalog unavailable in unit tests")


def test_scopes_each_table_and_caps_limit():
    sql = _validate_and_scope(
        "SELECT t.title, u.name FROM tasks AS t JOIN users AS u ON t.id = u.id LIMIT 500",
        SCHEMA,
        "tenant-1",
    )

    assert "t.tenant_id = 'tenant-1'" in sql
    assert "u.tenant_id = 'tenant-1'" in sql
    assert sql.endswith("LIMIT 200")


def test_escapes_tenant_id_as_a_sql_literal():
    sql = _validate_and_scope(
        "SELECT id FROM tasks",
        SCHEMA,
        "tenant' OR TRUE --",
    )

    assert "tenant'' OR TRUE --" in sql
    assert "tenant_id = 'tenant'' OR TRUE --'" in sql


def test_left_join_keeps_tenant_scope_in_join_condition():
    sql = _validate_and_scope(
        "SELECT t.title, u.name FROM tasks AS t LEFT JOIN users AS u ON t.id = u.id",
        SCHEMA,
        "tenant-1",
    )

    assert "WHERE t.tenant_id = 'tenant-1'" in sql
    assert "ON t.id = u.id AND u.tenant_id = 'tenant-1'" in sql
    assert "WHERE t.tenant_id = 'tenant-1' AND u.tenant_id" not in sql


def test_allows_boolean_operators_but_still_validates_functions():
    sql = _validate_and_scope(
        "SELECT id FROM tasks WHERE status = 'open' AND title = 'Closepacks'",
        SCHEMA,
        "tenant-1",
    )

    assert "status = 'open' AND title = 'Closepacks'" in sql
    assert "tenant_id = 'tenant-1'" in sql


def test_plan_sql_uses_gemini_and_enforces_trusted_tenant(monkeypatch):
    class FakeGemini:
        def __init__(self):
            self.calls = []

        def generate(self, system_prompt, user_prompt, temperature=0.3):
            self.calls.append((system_prompt, user_prompt))
            assert temperature == 0
            if "Classify this untrusted user request:" in user_prompt:
                assert "public.tasks:" not in user_prompt
                assert "tenant-1" not in user_prompt
                return (
                    '{"allowed":true,"category":"close_loop_data",'
                    '"reason":"Read-only task count.","clarification":null}'
                )
            assert "public.tasks:" in user_prompt
            assert "tenant-1" not in user_prompt
            return "```sql\nSELECT COUNT(*) AS total FROM tasks\n```"

    monkeypatch.setattr(
        "db.sql_planner.get_settings",
        lambda: Settings(DB_ENABLED=True, DB_URL="postgresql://unused"),
    )
    monkeypatch.setattr("db.sql_planner._ensure_tenant_exists", lambda *_args: None)
    monkeypatch.setattr("db.sql_planner._load_schema", lambda _db_url: SCHEMA)
    monkeypatch.setattr("db.sql_planner._load_catalog", _no_catalog)
    monkeypatch.setattr("db.sql_planner._dry_run", lambda *_args: None)
    client = FakeGemini()

    sql = plan_sql("How many tasks are open?", tenant_id="tenant-1", client=client)

    assert "COUNT(*)" in sql
    assert "tenant_id = 'tenant-1'" in sql
    assert sql.endswith("LIMIT 200")
    assert len(client.calls) == 2


def test_plan_sql_does_not_query_or_generate_sql_after_guardrail_rejection(monkeypatch):
    class UnsafeGemini:
        def generate(self, _system_prompt, user_prompt, temperature=0.3):
            assert "Classify this untrusted user request:" in user_prompt
            return (
                '{"allowed":false,"category":"unsafe",'
                '"reason":"Write requests are not allowed."}'
            )

    monkeypatch.setattr(
        "db.sql_planner.get_settings",
        lambda: Settings(DB_ENABLED=True, DB_URL="postgresql://unused"),
    )
    monkeypatch.setattr(
        "db.sql_planner._ensure_tenant_exists",
        lambda *_args: pytest.fail("Tenant lookup must not run after guardrail rejection."),
    )

    with pytest.raises(SQLPlanningError, match="unsafe"):
        plan_sql("Delete all tasks.", tenant_id="tenant-1", client=UnsafeGemini())


def test_plan_sql_stays_disabled_when_database_feature_is_off():
    with pytest.raises(FeatureDisabled):
        plan_sql("How many tasks?", tenant_id="tenant-1")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM tasks",
        "SELECT password_hash FROM users",
        "SELECT * FROM private.tasks",
        "SELECT id FROM unknown_table",
        "SELECT pg_sleep(10) FROM tasks",
        "SELECT id FROM tasks RIGHT JOIN users ON tasks.id = users.id",
        "SELECT id FROM tasks; DELETE FROM tasks",
    ],
)
def test_rejects_unsafe_or_unavailable_sql(sql):
    with pytest.raises(SQLPlanningError):
        _validate_and_scope(sql, SCHEMA, "tenant-1")


def test_rejects_ambiguous_unqualified_columns():
    with pytest.raises(SQLPlanningError, match="ambiguous"):
        _validate_and_scope(
            "SELECT id FROM tasks JOIN users ON tasks.id = users.id",
            SCHEMA,
            "tenant-1",
        )


def test_enforces_requested_smaller_row_limit():
    sql = _validate_and_scope(
        "SELECT id FROM tasks LIMIT 100",
        SCHEMA,
        "tenant-1",
        max_rows=10,
    )

    assert sql.endswith("LIMIT 10")


@pytest.mark.parametrize(
    "sql",
    [
        # sensitive column reached through an alias with the same name
        "SELECT id AS password_hash, password_hash FROM users",
        "SELECT id AS password_hash FROM users GROUP BY password_hash",
        "SELECT name FROM users ORDER BY password_hash || ''",
        "SELECT id AS secret_x FROM users",
        'SELECT "password_hash" FROM users',
        "SELECT u.id FROM users AS u WHERE u.id IN (SELECT password_hash FROM users)",
        # writes, locks, set operations and table functions
        "SELECT id FROM tasks FOR UPDATE",
        "SELECT id INTO newtable FROM tasks",
        "SELECT id FROM tasks UNION SELECT id FROM users",
        "SELECT g FROM generate_series(1, 10) AS g",
        "WITH d AS (DELETE FROM tasks RETURNING id) SELECT id FROM d",
        "WITH RECURSIVE r AS (SELECT id FROM tasks) SELECT id FROM r",
        "WITH tasks AS (SELECT id FROM users) SELECT id FROM tasks",
        "SELECT id FROM pg_catalog.pg_class",
        "SELECT id FROM tasks WHERE id = (SELECT id FROM audit_logs LIMIT 1)",
        "SELECT pg_read_file('/etc/passwd') FROM tasks",
        "SELECT id FROM tasks LIMIT 1 OFFSET 0 FETCH FIRST 5 ROWS ONLY",
    ],
)
def test_rejects_bypass_attempts(sql):
    with pytest.raises(SQLPlanningError):
        _validate_and_scope(sql, SCHEMA, "tenant-1")


@pytest.mark.parametrize(
    "sql",
    [
        # Postgres folds unquoted names to lower case and keeps quoted ones exactly. The tenant filter
        # is written unquoted, so these used to send BOTH filters to `users` and leave `tasks` open.
        'SELECT "T".title FROM tasks "T", users t',
        'SELECT t.title FROM tasks t, users "T"',
        'SELECT "T".title FROM tasks AS "T" JOIN users AS t ON t.id = "T".id',
        'SELECT x.title FROM (SELECT "A".title FROM tasks "A", users a) x',
        'WITH "C" AS (SELECT t.id FROM tasks t) SELECT c.id FROM "C" c, users "c"',
        'SELECT "Tasks".title FROM "Tasks", users AS tasks',
        'SELECT "my alias".title FROM tasks AS "my alias"',
    ],
)
def test_quoted_capitalised_names_cannot_misdirect_the_tenant_filter(sql):
    with pytest.raises(SQLPlanningError):
        _validate_and_scope(sql, SCHEMA, "tenant-1")


def test_lower_case_quoted_and_upper_case_unquoted_aliases_still_work_and_stay_scoped():
    quoted = _validate_and_scope('SELECT "t".title FROM tasks AS "t"', SCHEMA, "tenant-1")
    unquoted_caps = _validate_and_scope("SELECT T.title FROM tasks AS T", SCHEMA, "tenant-1")
    for sql in (quoted, unquoted_caps):
        assert "tenant_id = 'tenant-1'" in sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT t.title FROM tasks t WHERE t.tenant_id = 'tenant-2'",
        "SELECT t.title FROM tasks t WHERE 1 = 1 OR t.tenant_id = 'tenant-2'",
        "SELECT t.title FROM tasks t WHERE t.tenant_id = 'tenant-1' OR t.tenant_id = 'tenant-2'",
        "SELECT t.title FROM tasks t WHERE t.status = 'OPEN' OR TRUE",
        "SELECT t.title FROM tasks t, users u",
        "SELECT x.title FROM (SELECT t.title FROM tasks t) x",
        "WITH a AS (SELECT t.title FROM tasks t) SELECT a.title FROM a",
        "SELECT t.title FROM tasks t WHERE t.id IN (SELECT u.id FROM users u)",
        "SELECT t.title FROM tasks t WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = t.id)",
        "SELECT t.title FROM tasks t LEFT JOIN users u ON TRUE OR u.id = t.id",
        "SELECT (SELECT COUNT(*) FROM users z) AS n FROM tasks t",
        "SELECT t.title FROM public.tasks t",
        "SELECT t.title FROM tasks t JOIN users u ON u.id = t.id JOIN tasks t2 ON TRUE",
        "SELECT t.title FROM tasks t UNION SELECT u.name FROM users u",
        "SELECT t.title FROM tasks t RIGHT JOIN users u ON u.id = t.id",
    ],
)
def test_hostile_queries_are_rejected_or_every_table_is_filtered_by_the_callers_tenant(sql):
    import re

    import sqlglot
    from sqlglot import exp

    try:
        out = _validate_and_scope(sql, SCHEMA, "tenant-1")
    except SQLPlanningError:
        return
    tree = sqlglot.parse_one(out, read="postgres")
    tables = [t for t in tree.find_all(exp.Table) if t.name.lower() in SCHEMA]
    # one filter per table, each a top-level AND term of its own scope (never inside an OR)
    for table in tables:
        scope = table.find_ancestor(exp.Select)
        join = table.parent if isinstance(table.parent, exp.Join) else None
        where = (join.args.get("on") if join is not None and join.args.get("side") else None) or scope.args["where"].this
        terms = []
        stack = [where]
        while stack:
            node = stack.pop()
            if isinstance(node, (exp.And, exp.Paren)) and not isinstance(node, exp.Or):
                stack.extend(c for c in (node.this if isinstance(node, exp.Paren) else node.left,
                                         None if isinstance(node, exp.Paren) else node.right) if c is not None)
            else:
                terms.append(node.sql())
        assert re.search(rf"\b{table.alias_or_name}\.tenant_id = 'tenant-1'", " ".join(terms), re.I), out


def test_scopes_tables_inside_subqueries_and_ctes():
    sql = _validate_and_scope(
        "WITH open_tasks AS (SELECT id, status FROM tasks WHERE status = 'open') "
        "SELECT u.name FROM users AS u "
        "WHERE u.id IN (SELECT o.id FROM open_tasks AS o) "
        "AND EXISTS (SELECT 1 FROM tasks AS t WHERE t.id = u.id)",
        SCHEMA,
        "tenant-1",
    )

    assert sql.count("tenant_id = 'tenant-1'") == 3
    assert "t.tenant_id = 'tenant-1'" in sql
    assert "u.tenant_id = 'tenant-1'" in sql
    assert "tasks.tenant_id = 'tenant-1'" in sql


def test_allows_harmless_keywords_inside_string_values():
    sql = _validate_and_scope(
        "SELECT id FROM tasks WHERE title ILIKE '%create%' OR title = 'drop; -- copy'",
        SCHEMA,
        "tenant-1",
    )

    assert "tenant_id = 'tenant-1'" in sql


def test_allows_common_analytics_constructs():
    sql = _validate_and_scope(
        "SELECT status, COUNT(DISTINCT id) AS total, "
        "CASE WHEN status = 'open' THEN 1 ELSE 0 END AS is_open, "
        "ROW_NUMBER() OVER (PARTITION BY status ORDER BY MAX(id)) AS position, "
        "DATE_TRUNC('month', NOW()) AS month "
        "FROM tasks GROUP BY status HAVING COUNT(*) > 1 ORDER BY total DESC",
        SCHEMA,
        "tenant-1",
    )

    assert "tenant_id = 'tenant-1'" in sql


def test_output_has_no_sql_comments():
    sql = _validate_and_scope("SELECT id /* hi */ FROM tasks -- bye", SCHEMA, "tenant-1")

    assert "/*" not in sql and "--" not in sql


def test_inner_join_with_where_and_group_by_is_fully_scoped():
    sql = _validate_and_scope(
        "SELECT u.name, COUNT(t.id) AS total FROM users AS u JOIN tasks AS t ON t.id = u.id "
        "WHERE t.status <> 'done' GROUP BY u.name HAVING COUNT(t.id) > 1 ORDER BY total DESC LIMIT 5",
        SCHEMA,
        "tenant-1",
    )

    assert "u.tenant_id = 'tenant-1'" in sql
    assert "t.tenant_id = 'tenant-1'" in sql
    assert sql.endswith("LIMIT 5")


def test_tenant_proof_rejects_an_unscoped_table():
    from db.sql_planner import _assert_tenant_scoped

    with pytest.raises(SQLPlanningError, match="tenant isolation"):
        _assert_tenant_scoped(
            "SELECT id FROM tasks AS t WHERE t.id IN (SELECT id FROM users AS u) AND t.tenant_id = 'x'",
            set(),
            "x",
        )
    with pytest.raises(SQLPlanningError, match="tenant isolation"):
        _assert_tenant_scoped(
            "SELECT id FROM tasks AS t WHERE t.tenant_id = 'x' OR 1 = 1", set(), "x"
        )


# ---- understanding, repair loop and clarification -------------------------------------------

ALLOWED = (
    '{"allowed":true,"category":"close_loop_data","reason":"Read-only question.","clarification":null}'
)


class ScriptedGemini:
    """First reply is the guardrail verdict; the rest are planner replies, in order."""

    def __init__(self, *planner_replies):
        self.replies = [ALLOWED, *planner_replies]
        self.prompts = []

    def generate(self, _system, user_prompt, temperature=0.3):
        self.prompts.append(user_prompt)
        return self.replies.pop(0)


def _patch_environment(monkeypatch, dry_run=lambda *_args: None):
    monkeypatch.setattr(
        "db.sql_planner.get_settings",
        lambda: Settings(DB_ENABLED=True, DB_URL="postgresql://unused"),
    )
    monkeypatch.setattr("db.sql_planner._ensure_tenant_exists", lambda *_args: None)
    monkeypatch.setattr("db.sql_planner._load_schema", lambda _db_url: SCHEMA)
    monkeypatch.setattr("db.sql_planner._load_catalog", _no_catalog)
    monkeypatch.setattr("db.sql_planner._dry_run", dry_run)


def test_plan_returns_interpretation_and_assumptions(monkeypatch):
    _patch_environment(monkeypatch)
    client = ScriptedGemini(
        '{"interpretation":"Count open tasks","assumptions":["open = not COMPLETED"],'
        '"needs_clarification":false,"clarification":null,'
        '"sql":"SELECT COUNT(t.id) AS open_tasks FROM tasks AS t WHERE t.status <> \'COMPLETED\'"}'
    )

    plan = plan_sql_detailed("hw many tsks r pending??", tenant_id="tenant-1", client=client)

    assert plan.interpretation == "Count open tasks"
    assert plan.assumptions == ["open = not COMPLETED"]
    assert plan.attempts == 1
    assert "tenant_id = 'tenant-1'" in plan.sql
    assert "today is" in client.prompts[1].lower()
    assert "overdue" in client.prompts[1].lower()  # glossary reaches the model


def test_validation_error_is_fed_back_and_repaired(monkeypatch):
    _patch_environment(monkeypatch)
    client = ScriptedGemini(
        '{"interpretation":"x","assumptions":[],"needs_clarification":false,"clarification":null,'
        '"sql":"SELECT t.nonexistent FROM tasks AS t"}',
        '{"interpretation":"x","assumptions":[],"needs_clarification":false,"clarification":null,'
        '"sql":"SELECT t.title FROM tasks AS t"}',
    )

    plan = plan_sql_detailed("list tasks", tenant_id="tenant-1", client=client)

    assert plan.attempts == 2
    assert "nonexistent" in client.prompts[2]  # the exact error is shown to the model
    assert "tenant-1" not in client.prompts[2]


def test_database_dry_run_error_is_repaired(monkeypatch):
    calls = []

    def dry_run(_url, sql):
        calls.append(sql)
        if "'complete'" in sql:
            raise psycopg.errors.InvalidTextRepresentation(
                'invalid input value for enum task_status: "complete"'
            )

    _patch_environment(monkeypatch, dry_run)
    client = ScriptedGemini(
        '{"sql":"SELECT t.id FROM tasks AS t WHERE t.status = \'complete\'"}',
        '{"sql":"SELECT t.id FROM tasks AS t WHERE t.status = \'COMPLETED\'"}',
    )

    plan = plan_sql_detailed("completed tasks", tenant_id="tenant-1", client=client)

    assert "COMPLETED" in plan.sql and len(calls) == 2
    assert "invalid input value for enum" in client.prompts[2]


def test_gives_up_after_max_attempts(monkeypatch):
    _patch_environment(monkeypatch)
    bad = '{"sql":"SELECT t.nope FROM tasks AS t"}'
    client = ScriptedGemini(bad, bad, bad)

    with pytest.raises(SQLPlanningError, match="after 3 attempts"):
        plan_sql_detailed("list tasks", tenant_id="tenant-1", client=client)


def test_connection_errors_are_not_treated_as_model_mistakes(monkeypatch):
    def dry_run(*_args):
        raise psycopg.OperationalError("connection refused")

    _patch_environment(monkeypatch, dry_run)
    client = ScriptedGemini('{"sql":"SELECT t.id FROM tasks AS t"}')

    with pytest.raises(psycopg.OperationalError):
        plan_sql_detailed("list tasks", tenant_id="tenant-1", client=client)


def test_model_can_ask_for_clarification(monkeypatch):
    _patch_environment(monkeypatch)
    client = ScriptedGemini(
        '{"needs_clarification":true,"clarification":"Which period do you mean?","sql":null}'
    )

    with pytest.raises(ClarificationNeeded) as error:
        plan_sql_detailed("show me that thing", tenant_id="tenant-1", client=client)

    assert error.value.question == "Which period do you mean?"


def test_dry_run_rejects_expensive_plans():
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, *_args):
            class Result:
                def fetchone(self_inner):
                    return {"QUERY PLAN": [{"Plan": {"Total Cost": 9e9}}]}

            return Result()

    import db.sql_planner as module

    original = module.get_db_connection
    module.get_db_connection = lambda _url: Conn()
    try:
        with pytest.raises(SQLPlanningError, match="too expensive"):
            module._dry_run("postgresql://x", "SELECT 1")
    finally:
        module.get_db_connection = original


def test_parse_plan_response_accepts_json_fenced_json_and_bare_sql():
    assert _parse_plan_response('{"sql":"SELECT 1"}').sql == "SELECT 1"
    assert _parse_plan_response('```json\n{"sql":"SELECT 2"}\n```').sql == "SELECT 2"
    assert _parse_plan_response("```sql\nSELECT 3\n```").sql == "SELECT 3"
    with pytest.raises(SQLPlanningError):
        _parse_plan_response("I cannot help with that")
    with pytest.raises(SQLPlanningError):
        _parse_plan_response('{"sql": null}')


EXAMPLE_SCHEMA = {
    "tasks": {"id", "title", "status", "due_date", "completed_at", "updated_at", "plan_id",
              "entity_id", "member_assigned_to_id", "rejection_note", "is_required", "tenant_id"},
    "users": {"id", "name", "tenant_id"},
    "plans": {"id", "period_id", "entity_id", "owner_id", "title", "tenant_id"},
    "periods": {"id", "label", "start_date", "end_date", "status", "tenant_id"},
    "entities": {"id", "name", "tenant_id"},
    "task_entity_assignees": {"id", "task_id", "user_id", "done", "done_at", "tenant_id"},
    "time_entries": {"id", "user_id", "duration_minutes", "entry_date", "tenant_id"},
}


@pytest.mark.parametrize(("question", "sql"), EXAMPLES, ids=[q for q, _ in EXAMPLES])
def test_prompt_examples_pass_the_validator(question, sql):
    scoped = _validate_and_scope(sql, EXAMPLE_SCHEMA, "tenant-1")

    assert "tenant_id = 'tenant-1'" in scoped
