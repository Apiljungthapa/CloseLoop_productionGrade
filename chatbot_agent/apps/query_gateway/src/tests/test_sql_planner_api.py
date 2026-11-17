import pytest
from fastapi import HTTPException

from app.routes import sql_planner as sql_api
from core.config import Settings
from core.exceptions import FeatureDisabled, LLMUnavailable


def enabled_settings(**overrides):
    values = {
        "DB_ENABLED": True,
        "DB_URL": "postgresql://test",
        "SQL_PLANNER_API_ENABLED": True,
        "SQL_PLANNER_API_KEY": "sql-test-key",
        "SQL_QUERY_EXECUTION_ENABLED": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_requires_configured_api_key(monkeypatch):
    monkeypatch.setattr(sql_api, "get_settings", lambda: enabled_settings(SQL_PLANNER_API_KEY=""))

    with pytest.raises(HTTPException) as error:
        sql_api.require_sql_api_key("sql-test-key")

    assert error.value.status_code == 503


def test_rejects_invalid_api_key(monkeypatch):
    monkeypatch.setattr(sql_api, "get_settings", enabled_settings)

    with pytest.raises(HTTPException) as error:
        sql_api.require_sql_api_key("wrong-key")

    assert error.value.status_code == 401


def test_rejects_when_sql_api_is_disabled(monkeypatch):
    monkeypatch.setattr(
        sql_api,
        "get_settings",
        lambda: enabled_settings(SQL_PLANNER_API_ENABLED=False),
    )

    with pytest.raises(HTTPException) as error:
        sql_api.require_sql_api_key("sql-test-key")

    assert error.value.status_code == 404


def test_returns_pure_sql_without_executing(monkeypatch):
    calls = []

    def fake_plan_sql(question, *, tenant_id, max_rows):
        calls.append((question, tenant_id, max_rows))
        return "SELECT count(*) FROM tasks WHERE tenant_id = 'tenant-1' LIMIT 10"

    monkeypatch.setattr(sql_api, "plan_sql", fake_plan_sql)
    response = sql_api.generate_sql(
        sql_api.SQLPlanRequest(
            question="How many tasks?",
            tenant_id="tenant-1",
            max_rows=10,
        )
    )

    assert response.media_type == "text/plain"
    assert response.body.decode() == (
        "SELECT count(*) FROM tasks WHERE tenant_id = 'tenant-1' LIMIT 10"
    )
    assert calls == [("How many tasks?", "tenant-1", 10)]


@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [
        (sql_api.SQLPlanningError, 422),
        (FeatureDisabled, 503),
        (LLMUnavailable, 502),
    ],
)
def test_maps_planner_errors_to_http_status(monkeypatch, error_type, status_code):
    def fail_plan_sql(*_args, **_kwargs):
        raise error_type("Planner error.")

    monkeypatch.setattr(sql_api, "plan_sql", fail_plan_sql)

    with pytest.raises(HTTPException) as error:
        sql_api.generate_sql(
            sql_api.SQLPlanRequest(question="How many tasks?", tenant_id="tenant-1")
        )

    assert error.value.status_code == status_code


def test_sql_ask_plans_executes_and_generates_grounded_answer(monkeypatch):
    calls = []

    def fake_plan(question, *, tenant_id, max_rows, client):
        calls.append(("plan", question, tenant_id, max_rows))
        return sql_api.SQLPlan(
            "SELECT count(*) FROM tasks WHERE tenant_id = 'tenant-1' LIMIT 10",
            "Count all tasks",
            ["all statuses included"],
        )

    def fake_execute(sql, *, tenant_id, max_rows):
        calls.append(("execute", tenant_id, max_rows))
        assert "tenant_id = 'tenant-1'" in sql
        return [{"count": 3}]

    def fake_answer(question, rows, _client, context=None):
        calls.append(("answer", question, rows))
        assert context["interpretation"] == "Count all tasks"
        return "There are 3 tasks.", False

    monkeypatch.setattr(sql_api, "get_settings", enabled_settings)
    monkeypatch.setattr(sql_api, "plan_sql_detailed", fake_plan)
    monkeypatch.setattr(sql_api.executor, "execute", fake_execute)
    monkeypatch.setattr(sql_api.executor, "generate_answer", fake_answer)

    response = sql_api.ask_database(
        sql_api.SQLQueryRequest(
            question="How many tasks?",
            tenant_id="tenant-1",
            max_rows=10,
        )
    )

    assert response.answer == "There are 3 tasks."
    assert response.row_count == 1
    assert response.sql.endswith("LIMIT 10")
    assert not response.result_data_truncated
    assert response.interpretation == "Count all tasks"
    assert response.assumptions == ["all statuses included"]
    assert not response.needs_clarification
    assert calls == [
        ("plan", "How many tasks?", "tenant-1", 10),
        ("execute", "tenant-1", 10),
        ("answer", "How many tasks?", [{"count": 3}]),
    ]


def test_sql_ask_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        sql_api,
        "get_settings",
        lambda: enabled_settings(SQL_QUERY_EXECUTION_ENABLED=False),
    )

    with pytest.raises(HTTPException) as error:
        sql_api.ask_database(
            sql_api.SQLQueryRequest(question="How many tasks?", tenant_id="tenant-1")
        )

    assert error.value.status_code == 404


def test_sql_ask_returns_clarification_without_executing(monkeypatch):
    def fake_plan(*_args, **_kwargs):
        raise sql_api.ClarificationNeeded("Which period do you mean?")

    monkeypatch.setattr(sql_api, "get_settings", enabled_settings)
    monkeypatch.setattr(sql_api, "plan_sql_detailed", fake_plan)
    monkeypatch.setattr(
        sql_api.executor, "execute", lambda *_a, **_k: pytest.fail("must not execute")
    )

    response = sql_api.ask_database(
        sql_api.SQLQueryRequest(question="show it", tenant_id="tenant-1")
    )

    assert response.needs_clarification
    assert response.answer == "Which period do you mean?"
    assert response.sql is None
