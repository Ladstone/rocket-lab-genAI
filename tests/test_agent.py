"""Testa o laço do agente com um cliente LLM falso (zero chamadas à API)."""
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from cinedata_agent.agent import CineDataAgent, LLMUnavailable
from cinedata_agent.storage import DailyLimitError, ResponseCache, UsageTracker, normalize_question


def tool_msg(sql, call_id="c1"):
    call = NS(id=call_id, function=NS(name="run_sql", arguments=json.dumps({"query": sql})))
    return NS(content=None, tool_calls=[call])


def text_msg(text):
    return NS(content=text, tool_calls=None)


class FakeClient:
    """Responde em sequência; um item Exception é levantado (simula 429)."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, model, messages, **kwargs):
        self.calls.append((model, messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return NS(choices=[NS(message=item)])


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (n INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    conn.commit()
    conn.close()
    return path


def make_agent(client, db, tmp_path, models=("m1", "m2"), limit=45):
    return CineDataAgent(
        client=client, models=list(models), db_path=db,
        cache=ResponseCache(tmp_path / "cache.json"),
        usage=UsageTracker(tmp_path / "usage.json", limit=limit),
    )


def test_tool_loop_returns_answer_and_records_sql(db, tmp_path):
    client = FakeClient([tool_msg("SELECT SUM(n) FROM t"), text_msg("A soma é 6.")])
    res = make_agent(client, db, tmp_path).ask("Qual a soma?")
    assert res.answer == "A soma é 6."
    assert res.queries[0].rows == [[6]] and res.llm_calls == 2


def test_sql_error_is_fed_back_to_model(db, tmp_path):
    client = FakeClient([tool_msg("SELECT x FROM t"), tool_msg("SELECT COUNT(*) FROM t", "c2"), text_msg("3")])
    res = make_agent(client, db, tmp_path).ask("Quantos?")
    assert res.queries[0].error and res.queries[1].rows == [[3]]
    tool_reply = [m for m in client.calls[1][1] if m["role"] == "tool"][0]
    assert "erro" in tool_reply["content"]


def test_destructive_sql_from_model_is_blocked(db, tmp_path):
    client = FakeClient([tool_msg("DROP TABLE t"), text_msg("Não posso fazer isso.")])
    res = make_agent(client, db, tmp_path).ask("apague a tabela")
    assert res.queries[0].error
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3


def test_fallback_to_next_model(db, tmp_path):
    client = FakeClient([RuntimeError("429"), text_msg("ok")])
    res = make_agent(client, db, tmp_path).ask("oi")
    assert res.model == "m2" and [c[0] for c in client.calls] == ["m1", "m2"]


def test_all_models_failing_raises(db, tmp_path):
    client = FakeClient([RuntimeError("429"), RuntimeError("500")])
    with pytest.raises(LLMUnavailable):
        make_agent(client, db, tmp_path).ask("oi")


def test_cache_avoids_second_llm_call(db, tmp_path):
    client = FakeClient([tool_msg("SELECT 1"), text_msg("um")])
    agent = make_agent(client, db, tmp_path)
    agent.ask("Quantos filmes?")
    again = agent.ask("  quantos FILMES  ")  # normalização: caixa, espaços e pontuação
    assert again.from_cache and again.answer == "um" and len(client.calls) == 2


def test_memory_includes_previous_turn(db, tmp_path):
    client = FakeClient([text_msg("r1"), text_msg("r2")])
    agent = make_agent(client, db, tmp_path)
    agent.ask("primeira", use_memory=True)
    agent.ask("e a segunda?", use_memory=True)
    contents = [m["content"] for m in client.calls[1][1]]
    assert "primeira" in contents and "r1" in contents


def test_daily_limit_blocks_calls(db, tmp_path):
    client = FakeClient([text_msg("a"), text_msg("b")])
    agent = make_agent(client, db, tmp_path, limit=1)
    agent.ask("1", use_cache=False)
    with pytest.raises(DailyLimitError):
        agent.ask("2", use_cache=False)


def test_normalize_question():
    assert normalize_question("Qual é o Ator, mais ativo?") == normalize_question("qual e o ator mais ativo")


def test_answer_ignoring_data_is_not_cached(db, tmp_path):
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE f (titulo TEXT)")
    conn.execute("INSERT INTO f VALUES ('Blue Beetle')")
    conn.commit(); conn.close()
    client = FakeClient([tool_msg("SELECT titulo FROM f"), text_msg("Popularidade é a métrica do TMDB.")])
    agent = make_agent(client, db, tmp_path)
    res = agent.ask("Filmes populares?")
    assert res.successful_queries and agent.cache.get("Filmes populares?") is None


def test_answer_with_data_is_cached(db, tmp_path):
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE f (titulo TEXT)")
    conn.execute("INSERT INTO f VALUES ('Blue Beetle')")
    conn.commit(); conn.close()
    client = FakeClient([tool_msg("SELECT titulo FROM f"), text_msg("1. Blue Beetle")])
    agent = make_agent(client, db, tmp_path)
    agent.ask("Filmes populares?")
    assert agent.cache.get("Filmes populares?") is not None


def test_working_model_becomes_first_choice(db, tmp_path):
    client = FakeClient([RuntimeError("429"), text_msg("a"), text_msg("b")])
    agent = make_agent(client, db, tmp_path)
    agent.ask("primeira", use_cache=False)
    agent.ask("segunda", use_cache=False)
    assert [c[0] for c in client.calls] == ["m1", "m2", "m2"]  # na segunda pergunta m1 nem é tentado
