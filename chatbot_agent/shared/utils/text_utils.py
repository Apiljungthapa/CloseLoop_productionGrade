import re


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b[\w-]+\b", str(text).lower())


def keyword_overlap(text: str, query_tokens: set[str]) -> float:
    """Fraction of query tokens that appear in the text."""
    if not query_tokens:
        return 0.0
    text_tokens = set(tokenize(text))
    return len(query_tokens & text_tokens) / len(query_tokens) if text_tokens else 0.0


def fingerprint(text: str) -> str:
    """Whitespace/case-insensitive key used to detect duplicate chunks."""
    return " ".join(str(text).split()).lower()
