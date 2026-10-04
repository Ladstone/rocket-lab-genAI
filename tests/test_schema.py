"""Garante que o esquema descrito no prompt bate com o banco real (pulado se o .db não existir)."""
import sqlite3

import pytest

from cinedata_agent.config import settings
from cinedata_agent.prompts import SCHEMA, build_system_prompt

pytestmark = pytest.mark.skipif(not settings.db_path.exists(), reason="cinerocket.db não encontrado")


def test_schema_matches_database():
    conn = sqlite3.connect(f"file:{settings.db_path.as_posix()}?mode=ro", uri=True)
    for table, cols in SCHEMA.items():
        real = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        assert real, f"tabela {table} não existe"
        assert set(cols) == set(real), f"colunas divergentes em {table}"


def test_prompt_mentions_all_tables():
    prompt = build_system_prompt()
    assert all(t in prompt for t in SCHEMA)
