from shared.schemas.retrieval_result import RetrievedItem
from shared.utils.text_utils import fingerprint, keyword_overlap, tokenize


def rerank(
    query: str,
    items: list[RetrievedItem],
    semantic_weight: float = 0.75,
    min_score: float = 0.0,
    top_k: int = 5,
) -> list[RetrievedItem]:
    """Blend semantic score with keyword overlap, drop duplicates and weak hits."""
    tokens = set(tokenize(query))
    seen: set[str] = set()
    out: list[RetrievedItem] = []
    for item in items:
        key = fingerprint(item.text)
        if not key or key in seen:
            continue
        seen.add(key)
        semantic = item.semantic_score if item.semantic_score is not None else item.score
        keyword = keyword_overlap(item.text, tokens)
        score = semantic * semantic_weight + keyword * (1 - semantic_weight)
        if score < min_score:
            continue
        out.append(item.model_copy(update={
            "semantic_score": round(semantic, 6),
            "keyword_score": round(keyword, 6),
            "score": round(score, 6),
        }))
    out.sort(key=lambda i: i.score, reverse=True)
    return out[:top_k]
