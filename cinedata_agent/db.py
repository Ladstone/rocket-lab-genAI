"""Execução segura de SQL: somente leitura, uma instrução, com limite de linhas e tempo."""
import re
import sqlite3
import time
from pathlib import Path

from .config import settings

_SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    _SQLITE_RECURSIVE,
}
_MAX_CELL_CHARS = 200  # evita despejar sinopses inteiras no contexto do modelo


class QueryError(Exception):
    """Consulta recusada pelos guardrails ou que falhou na execução."""


def _authorizer(action, arg1, arg2, db_name, source):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def validate_sql(sql: str) -> str:
    """Valida e normaliza a consulta. Levanta QueryError se não for um SELECT simples."""
    if not isinstance(sql, str) or not sql.strip():
        raise QueryError("Consulta vazia.")
    cleaned = sql.strip().rstrip(";").strip()
    if ";" in cleaned:
        raise QueryError("Envie apenas uma instrução SQL por vez.")
    if not re.match(r"(?is)^(select|with)\b", cleaned):
        raise QueryError("Apenas consultas de leitura (SELECT / WITH) são permitidas.")
    return cleaned


def execute_readonly(
    sql: str,
    db_path: Path | str | None = None,
    max_rows: int | None = None,
    timeout_s: float | None = None,
) -> dict:
    """Executa um SELECT e devolve {'columns', 'rows', 'truncated'}.

    Camadas de proteção: (1) validação textual; (2) conexão SQLite em modo `mode=ro`;
    (3) authorizer que nega qualquer ação além de leitura; (4) limite de linhas; (5) timeout.
    """
    db_path = Path(db_path or settings.db_path)
    max_rows = max_rows or settings.max_rows
    timeout_s = timeout_s or settings.query_timeout_s
    sql = validate_sql(sql)

    if not db_path.exists():
        raise QueryError(f"Banco não encontrado em {db_path}.")

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        conn.set_authorizer(_authorizer)
        deadline = time.monotonic() + timeout_s
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 100_000)
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(max_rows + 1)
        except sqlite3.Error as exc:
            msg = str(exc)
            if "interrupted" in msg:
                msg = f"Consulta excedeu {timeout_s:.0f}s. Simplifique filtros/junções ou use LIMIT."
            raise QueryError(msg) from exc
    finally:
        conn.close()

    truncated = len(fetched) > max_rows
    rows = []
    for row in fetched[:max_rows]:
        rows.append([
            v[:_MAX_CELL_CHARS] + "…" if isinstance(v, str) and len(v) > _MAX_CELL_CHARS else v
            for v in row
        ])
    return {"columns": columns, "rows": rows, "truncated": truncated}
