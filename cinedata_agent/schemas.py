"""Modelos pydantic: contratos de entrada/saída do agente, do cache e da avaliação."""
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class RunSqlArgs(BaseModel):
    """Argumentos da ferramenta run_sql, como o LLM os envia (validados antes de executar)."""

    query: str = Field(description="Consulta SQL SELECT/WITH (SQLite).")

    @field_validator("query")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a consulta não pode ser vazia")
        return v


def run_sql_parameters() -> dict:
    """JSON Schema dos parâmetros da tool, derivado do modelo (sem os 'title' que sobram do pydantic)."""
    def strip_titles(node):
        if isinstance(node, dict):
            return {k: strip_titles(v) for k, v in node.items() if k != "title"}
        return node
    return strip_titles(RunSqlArgs.model_json_schema())


class QueryRecord(BaseModel):
    """Uma execução de SQL feita pelo agente (sucesso ou erro)."""

    sql: str
    columns: list[str] = []
    rows: list[list[Any]] = []
    truncated: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class AgentResult(BaseModel):
    answer: str
    queries: list[QueryRecord] = []
    model: str | None = None
    from_cache: bool = False
    llm_calls: int = 0

    @property
    def successful_queries(self) -> list[QueryRecord]:
        return [q for q in self.queries if q.ok]


class CacheEntry(BaseModel):
    answer: str
    queries: list[QueryRecord] = []
    model: str | None = None


class EvalQuestion(BaseModel):
    id: str
    category: str
    question: str
    reference_sql: str
    match: Literal["metric"] | None = None  # 'metric': compara só as métricas do ranking (há empates)
