from shared.constants.enums import EvidenceSource
from shared.schemas.retrieval_result import RetrievedItem


def rows_to_evidence(question: str, sql: str, rows: list[dict], max_rows: int = 50) -> list[RetrievedItem]:
    """Turn query rows into one evidence item the answer prompt can use."""
    shown = rows[:max_rows]
    lines = [" | ".join(f"{k}={v}" for k, v in row.items()) for row in shown]
    suffix = f"\n(+{len(rows) - max_rows} more rows)" if len(rows) > max_rows else ""
    return [RetrievedItem(
        source=EvidenceSource.DATABASE,
        text="\n".join(lines) + suffix if lines else "(query returned no rows)",
        score=1.0,
        title="Database",
        metadata={"sql": sql, "row_count": len(rows)},
    )]
