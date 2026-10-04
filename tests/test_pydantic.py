import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cinedata_agent.agent import TOOLS
from cinedata_agent.config import ROOT, Settings
from cinedata_agent.schemas import AgentResult, EvalQuestion, QueryRecord, RunSqlArgs
from cinedata_agent.storage import ResponseCache


def make_settings(monkeypatch, **env):
    for k in ("OPENROUTER_MODELS", "CINEROCKET_DB", "MAX_STEPS", "DAILY_LIMIT", "MAX_ROWS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_models_parsed_from_comma_separated_env(monkeypatch):
    s = make_settings(monkeypatch, OPENROUTER_MODELS=" a:free , b:free ,")
    assert s.models == ["a:free", "b:free"]


def test_defaults_and_relative_db_path(monkeypatch):
    s = make_settings(monkeypatch)
    assert s.db_path == ROOT / "cinerocket.db" and len(s.models) >= 1 and s.max_rows == 50


def test_absolute_db_path_is_kept(monkeypatch, tmp_path):
    assert make_settings(monkeypatch, CINEROCKET_DB=str(tmp_path / "x.db")).db_path == tmp_path / "x.db"


@pytest.mark.parametrize("env", [{"MAX_STEPS": "0"}, {"DAILY_LIMIT": "abc"}, {"MAX_ROWS": "-3"}, {"OPENROUTER_MODELS": " , "}])
def test_invalid_settings_fail_fast(monkeypatch, env):
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, **env)


def test_run_sql_args_validation():
    assert RunSqlArgs.model_validate_json('{"query": "SELECT 1"}').query == "SELECT 1"
    for bad in ['{}', '{"query": "   "}', '{"query": 5}', "não é json"]:
        with pytest.raises(ValidationError):
            RunSqlArgs.model_validate_json(bad)


def test_tool_schema_is_derived_from_model():
    params = TOOLS[0]["function"]["parameters"]
    assert params["required"] == ["query"] and params["properties"]["query"]["type"] == "string"
    assert "title" not in json.dumps(params)


def test_query_record_and_result_helpers():
    ok, bad = QueryRecord(sql="SELECT 1", columns=["n"], rows=[[1]]), QueryRecord(sql="x", error="boom")
    res = AgentResult(answer="a", queries=[bad, ok])
    assert ok.ok and not bad.ok and res.successful_queries == [ok]


def test_eval_questions_file_is_valid():
    data = json.loads((Path(__file__).parent.parent / "eval" / "questions.json").read_text(encoding="utf-8"))
    qs = [EvalQuestion(**d) for d in data]
    assert len(qs) == 14 and len({q.id for q in qs}) == 14
    assert {q.category for q in qs} == {"financas", "popularidade", "elenco", "generos_produtoras", "avaliacoes"}


def test_corrupted_cache_entry_is_treated_as_miss(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"qual a soma": {"resposta_antiga": "x"}}), encoding="utf-8")
    assert ResponseCache(path).get("Qual a soma?") is None
