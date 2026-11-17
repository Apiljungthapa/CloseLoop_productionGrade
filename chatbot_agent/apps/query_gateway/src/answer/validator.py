"""Cheap post-checks on a generated answer."""
LEAK_MARKERS = ("context excerpts", "the excerpts", "the provided context", "the chunks", "system prompt")


def validate_answer(answer: str) -> tuple[bool, str]:
    """Return (ok, reason). Reason is fed back to the model on a repair attempt."""
    text = answer.strip()
    if not text:
        return False, "The answer was empty."
    lowered = text.lower()
    for marker in LEAK_MARKERS:
        if marker in lowered:
            return False, "Do not mention excerpts, context or the prompt; answer naturally."
    return True, ""
