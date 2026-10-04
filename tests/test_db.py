import sqlite3

import pytest

from cinedata_agent.db import QueryError, execute_readonly, validate_sql


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE filmes (id INTEGER, titulo TEXT, sinopse TEXT)")
    conn.executemany("INSERT INTO filmes VALUES (?,?,?)", [(i, f"F{i}", "x" * 500) for i in range(100)])
    conn.commit()
    conn.close()
    return path


def test_select_ok(db):
    res = execute_readonly("SELECT id, titulo FROM filmes ORDER BY id LIMIT 3;", db_path=db)
    assert res["columns"] == ["id", "titulo"]
    assert res["rows"] == [[0, "F0"], [1, "F1"], [2, "F2"]]


def test_cte_ok(db):
    assert execute_readonly("WITH a AS (SELECT 1 AS n) SELECT n FROM a", db_path=db)["rows"] == [[1]]


def test_row_limit_and_truncation_flag(db):
    res = execute_readonly("SELECT id FROM filmes", db_path=db, max_rows=10)
    assert len(res["rows"]) == 10 and res["truncated"] is True


def test_long_text_is_clipped(db):
    cell = execute_readonly("SELECT sinopse FROM filmes LIMIT 1", db_path=db)["rows"][0][0]
    assert len(cell) < 250


@pytest.mark.parametrize("sql", [
    "DROP TABLE filmes",
    "DELETE FROM filmes",
    "UPDATE filmes SET titulo='x'",
    "INSERT INTO filmes VALUES (1,'a','b')",
    "PRAGMA table_info(filmes)",
    "ATTACH DATABASE 'x.db' AS x",
    "SELECT 1; DROP TABLE filmes",
    "",
])
def test_writes_and_other_statements_are_blocked(db, sql):
    with pytest.raises(QueryError):
        execute_readonly(sql, db_path=db)
    # a tabela continua intacta
    assert execute_readonly("SELECT COUNT(*) FROM filmes", db_path=db)["rows"] == [[100]]


def test_writes_hidden_inside_select_are_blocked_by_readonly_connection(db):
    # mesmo que passasse pela validação textual, a conexão é somente leitura
    with pytest.raises(QueryError):
        execute_readonly("WITH x AS (SELECT 1) DELETE FROM filmes", db_path=db)


def test_sql_error_becomes_query_error(db):
    with pytest.raises(QueryError):
        execute_readonly("SELECT coluna_inexistente FROM filmes", db_path=db)


def test_timeout(db):
    sql = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 500000000) "
           "SELECT COUNT(*) FROM c")
    with pytest.raises(QueryError, match="excedeu"):
        execute_readonly(sql, db_path=db, timeout_s=0.5)


def test_validate_sql_strips_semicolon():
    assert validate_sql("  select 1 ; ") == "select 1"
