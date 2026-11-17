from answer.prompt import SYSTEM_PROMPT, build_prompt, format_context
from shared.constants.enums import EvidenceSource
from shared.schemas.retrieval_result import RetrievedItem


def item(text, title="Policy", section=None, source=EvidenceSource.DOCUMENT):
    return RetrievedItem(text=text, title=title, section=section, source=source)


def test_format_context_numbers_sources_and_skips_empty():
    out = format_context([item("Refunds take 30 days.", section="Refunds"), item("   ")])
    assert "[1] Source: Policy - Refunds" in out
    assert "Refunds take 30 days." in out
    assert "[2]" not in out


def test_database_evidence_is_labelled():
    out = format_context([item("count=3", title="Database", source=EvidenceSource.DATABASE)])
    assert "Database result" in out


def test_build_prompt_has_question_and_context():
    prompt = build_prompt(" What is the fee? ", [item("Fee is $5.")])
    assert "What is the fee?" in prompt and "Fee is $5." in prompt


def test_empty_context_placeholder():
    assert "no relevant excerpts" in format_context([])


def test_system_prompt_forbids_guessing():
    assert "Do not invent facts" in SYSTEM_PROMPT


def test_system_prompt_asks_for_every_part_of_a_multi_part_question():
    assert "answer EVERY part" in SYSTEM_PROMPT
    assert "Never\n   leave a part out" in SYSTEM_PROMPT or "leave a part out" in SYSTEM_PROMPT
