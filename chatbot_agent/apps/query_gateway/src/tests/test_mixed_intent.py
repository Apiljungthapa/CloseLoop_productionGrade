"""A message that asks for documentation AND for records must use both sources."""
import pytest

from planner.intent_router import classify
from planner.mixed_intent import classify_part, detect_mixed_intent, split_parts
from shared.constants.enums import EvidenceSource, Route
from tests.test_workflow import Harness, nodes, route_reply, run


# --- the pure split ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "message, doc, db",
    [
        ("what is close packs and who is devraj khatri", "what is close packs", "who is devraj khatri"),
        ("who is devraj khatri and what is a close pack", "what is a close pack", "who is devraj khatri"),
        ("WHAT IS CLOSE PACKS AND WHO IS DEVRAJ KHATRI", "WHAT IS CLOSE PACKS", "WHO IS DEVRAJ KHATRI"),
        ("what is close packs; who is devraj khatri", "what is close packs", "who is devraj khatri"),
        ("what is close packs\nwho is devraj khatri", "what is close packs", "who is devraj khatri"),
        ("what is close packs & who is devraj khatri", "what is close packs", "who is devraj khatri"),
        ("what's a close pack and who's devraj khatri", "what's a close pack", "who's devraj khatri"),
        ("what is a close pack? who is Binita Gopali? how many entities do we have?",
         "what is a close pack", "who is Binita Gopali and how many entities do we have"),
        ("define overdue and tell me how many tasks are overdue", "define overdue", "tell me how many tasks are overdue"),
        ("explain carry forward and how many tasks were carried forward",
         "explain carry forward", "how many tasks were carried forward"),
        ("what does a team lead do & who are the team leads", "what does a team lead do", "who are the team leads"),
        ("how do I create a task and how many tasks are in progress",
         "how do I create a task", "how many tasks are in progress"),
        ("what is close packs and which tasks are due today", "what is close packs", "which tasks are due today"),
        # romanised Nepali / Hindi
        ("close pack ke ho ra devraj khatri ko ho", "close pack ke ho", "devraj khatri ko ho"),
        ("close pack kya hota hai and kitne users hain", "close pack kya hota hai", "kitne users hain"),
    ],
)
def test_documentation_plus_records_is_mixed(message, doc, db):
    mixed = detect_mixed_intent(message)
    assert mixed is not None, message
    assert mixed.doc_query == doc
    assert mixed.db_query == db


@pytest.mark.parametrize(
    "message, doc, db",
    [
        ("devraj khatri and close pack", "what is close pack", "devraj khatri"),
        ("close packs and devraj khatri", "what is close packs", "devraj khatri"),
        ("close pack, devraj khatri, binita gopali", "what is close pack", "devraj khatri and binita gopali"),
        ("devraj khatri? and what is a close pack", "what is a close pack", "devraj khatri"),
    ],
)
def test_bare_terms_without_a_question_word_are_mixed(message, doc, db):
    mixed = detect_mixed_intent(message)
    assert mixed is not None, message
    assert (mixed.doc_query, mixed.db_query) == (doc, db)


@pytest.mark.parametrize(
    "message",
    [
        "what is a close pack",  # one part
        "who is devraj khatri",
        "how many tasks are overdue",
        "who is devraj khatri and how many tasks does he have",  # two record parts
        "how many tasks are completed and how many are overdue",
        "list all tasks and periods",  # two lists, not an explanation
        "explain the close process and explain the refund policy",  # two documentation parts
        "what is a close pack and what is a period",
        "devraj khatri, tasks",  # a generic word next to a name means "his tasks"
        # a follow-on clause about the same records is still ONE records question (regression: it dropped "names")
        "How many entities do we have and what are their names?",
        "who are the team leads and what are their emails",
        "list the users and what are their roles",
        "show me devraj khatri and what is his role",
        "who is devraj khatri and what is his email",
        "how many tasks are completed and what is the completion rate",
        "how many tasks are overdue and what is the oldest one",
        "who has the most tasks and what are they",
        "hello and thanks",
        "",
    ],
)
def test_single_source_messages_are_not_mixed(message):
    assert detect_mixed_intent(message) is None, message


def test_how_to_wording_is_documentation_even_when_it_names_a_record_word():
    assert classify_part("how do I list tasks") == "doc"
    assert classify_part("show me how to create a plan") == "doc"
    assert classify_part("show me the tasks") == "db"


def test_status_words_alone_do_not_turn_a_definition_into_a_records_question():
    assert classify_part("define overdue") == "doc"
    assert classify_part("what is overdue") == "doc"
    assert classify_part("tasks overdue") == "db"
    assert classify_part("what is the current period") == "db"


def test_split_parts_caps_the_number_of_parts():
    assert len(split_parts("a b, c d, e f, g h, i j, k l, m n, o p")) == 6
    assert split_parts("  ,; ?? ") == []


# --- the keyword fallback ---------------------------------------------------------------------
def test_rules_route_a_mixed_message_to_hybrid():
    route, notes = classify("what is close packs and who is devraj khatri")
    assert route == Route.HYBRID
    route, notes = classify("devraj khatri and close pack")
    assert route == Route.HYBRID and any("mixes" in n for n in notes)


def test_rules_leave_single_source_messages_alone():
    assert classify("Explain the refund policy")[0] == Route.DOC
    assert classify("how many tasks are overdue")[0] == Route.DB
    assert classify("who is devraj khatri")[0] == Route.DB
    assert classify("what is a close pack")[0] == Route.DOC
    assert classify("Hello!")[0] == Route.CHITCHAT


# --- the graph --------------------------------------------------------------------------------
@pytest.mark.parametrize("wrong_route", ["doc", "db"])
def test_graph_uses_both_sources_even_if_the_router_picked_one(wrong_route):
    harness = Harness(router=route_reply(wrong_route, "x", "x"), barrier=True)  # barrier: they must run in parallel

    state = run(harness, "what is close packs and who is devraj khatri")

    assert state["route"] == "hybrid"
    assert harness.doc_calls == ["what is close packs"]
    assert harness.db_calls == [("who is devraj khatri", "tenant-1", 50)]
    assert {i.source for i in harness.generator.calls[0][1]} == {EvidenceSource.DOCUMENT, EvidenceSource.DATABASE}
    assert nodes(state).count("merge") == 1 and nodes(state)[-1] == "generate"
    assert "mixes a documentation question" in state["route_reason"]


def test_graph_catches_bare_terms_the_router_called_records():
    harness = Harness(router=route_reply("db", "devraj khatri and close pack", "devraj khatri and close pack"))

    state = run(harness, "devraj khatri and close pack")

    assert state["route"] == "hybrid"
    assert harness.doc_calls == ["what is close pack"]
    assert [call[0] for call in harness.db_calls] == ["devraj khatri"]


def test_graph_keeps_the_routers_own_hybrid_split():
    harness = Harness(router=route_reply("hybrid", "explain close", "overdue tasks"))

    state = run(harness, "what is close packs and who is devraj khatri")

    assert state["route"] == "hybrid"
    assert harness.doc_calls == ["explain close"]
    assert [call[0] for call in harness.db_calls] == ["overdue tasks"]
    assert "mixes" not in state["route_reason"]


def test_graph_does_not_touch_a_single_source_question():
    harness = Harness(router=route_reply("db", db_query="devraj khatri"))

    state = run(harness, "who is devraj khatri")

    assert state["route"] == "db"
    assert harness.doc_calls == [] and [call[0] for call in harness.db_calls] == ["devraj khatri"]


def test_graph_without_database_access_answers_the_documentation_half_only():
    harness = Harness(router=route_reply("doc", "x", "x"))

    state = run(harness, "what is close packs and who is devraj khatri", db_allowed=False)

    assert state["route"] == "doc"
    assert harness.db_calls == [] and len(harness.doc_calls) == 1
    assert any("answering from documents only" in w for w in state["warnings"])


def test_graph_mixed_when_the_router_model_is_down(monkeypatch):
    from core.exceptions import LLMUnavailable

    harness = Harness(router_error=LLMUnavailable("down"))

    state = run(harness, "what is close packs and who is devraj khatri")

    assert state["route"] == "hybrid" and state["route_source"] == "rules"
    assert harness.doc_calls and harness.db_calls
