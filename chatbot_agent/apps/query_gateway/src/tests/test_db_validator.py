import pytest

from db.sql_validator import UnsafeSQL, validate_sql


def test_strips_semicolon():
    assert validate_sql("SELECT a FROM tasks;") == "SELECT a FROM tasks"


def test_keeps_harmless_keywords_in_values():
    sql = "SELECT a FROM t WHERE b = 'drop; -- copy'"
    assert validate_sql(sql) == sql


@pytest.mark.parametrize("sql", [
    "DROP TABLE tasks", "UPDATE t SET a=1", "", "  ;", "EXPLAIN SELECT 1", "CALL f()",
])
def test_rejects_non_select(sql):
    with pytest.raises(UnsafeSQL):
        validate_sql(sql)
