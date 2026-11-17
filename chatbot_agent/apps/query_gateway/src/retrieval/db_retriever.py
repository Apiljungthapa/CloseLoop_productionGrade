from db import executor, result_normalizer, sql_planner, sql_validator
from shared.schemas.retrieval_result import RetrievedItem


class DbRetriever:
    """Tenant-scoped question -> SQL -> validate -> execute -> evidence."""

    def retrieve(
        self,
        question: str,
        tenant_id: str,
        max_rows: int = 200,
    ) -> list[RetrievedItem]:
        sql = sql_validator.validate_sql(
            sql_planner.plan_sql(question, tenant_id=tenant_id, max_rows=max_rows),
            max_rows=max_rows,
        )
        rows = executor.execute(sql, tenant_id=tenant_id, max_rows=max_rows)
        return result_normalizer.rows_to_evidence(question, sql, rows)
