from planner.intent_router import classify
from planner.query_planner import QueryPlanner
from shared.constants.enums import Route
from shared.schemas.user_query import UserQuery


def test_greeting_is_chitchat():
    assert classify("Hello!")[0] == Route.CHITCHAT


def test_policy_question_is_doc():
    assert classify("Explain the refund policy")[0] == Route.DOC


def test_record_question_is_db_and_mixed_is_hybrid():
    assert classify("how many tasks are overdue")[0] == Route.DB
    assert classify("how many tasks are overdue and explain the policy")[0] == Route.HYBRID


def test_disabled_db_falls_back_to_doc():
    plan = QueryPlanner(db_enabled=False).plan(UserQuery(text="how many tasks are overdue"))
    assert plan.route == Route.DOC
    assert any("db disabled" in n for n in plan.notes)


def test_enabled_db_keeps_route():
    assert QueryPlanner(db_enabled=True).plan(UserQuery(text="how many tasks")).route == Route.DB
