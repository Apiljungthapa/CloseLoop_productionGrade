from loguru import logger

from shared.schemas.retrieval_result import RetrievedItem

from .generator import AnswerGenerator
from .validator import validate_answer

NOT_FOUND_ANSWER = "I couldn't find that in the available information."
CHITCHAT_REPLY = (
    "Hello! I am Close Bhai. You can ask me anything related to CloseLoop and its docs."
)


def generate_with_repair(
    generator: AnswerGenerator, question: str, evidence: list[RetrievedItem], max_repairs: int = 1
) -> str:
    """Generate, validate, and retry with the failure reason. Falls back to a safe message."""
    instruction = ""
    for attempt in range(max_repairs + 1):
        answer = generator.generate(question, evidence, extra_instruction=instruction)
        ok, reason = validate_answer(answer)
        if ok:
            return answer
        logger.warning(f"Answer rejected (attempt {attempt + 1}): {reason}")
        instruction = reason
    return NOT_FOUND_ANSWER
