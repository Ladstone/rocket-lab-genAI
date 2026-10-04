"""Cache de respostas e contador diário de chamadas ao LLM (arquivos JSON locais)."""
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from .config import settings
from .schemas import CacheEntry


class DailyLimitError(Exception):
    """Limite diário local de chamadas ao LLM atingido."""


def normalize_question(question: str) -> str:
    text = unicodedata.normalize("NFKD", question.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


class ResponseCache:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.cache_path)

    def get(self, question: str) -> CacheEntry | None:
        raw = _read_json(self.path, {}).get(normalize_question(question))
        if raw is None:
            return None
        try:
            return CacheEntry.model_validate(raw)
        except ValidationError:  # entrada de uma versão antiga/corrompida: trata como cache miss
            return None

    def set(self, question: str, entry: CacheEntry) -> None:
        data = _read_json(self.path, {})
        data[normalize_question(question)] = entry.model_dump()
        _write_json(self.path, data)


class UsageTracker:
    """Conta chamadas ao LLM por dia para não estourar as 50 requisições gratuitas."""

    def __init__(self, path: Path | None = None, limit: int | None = None):
        self.path = Path(path or settings.usage_path)
        self.limit = limit if limit is not None else settings.daily_limit

    def _load(self) -> dict:
        data = _read_json(self.path, {})
        if data.get("date") != date.today().isoformat():
            data = {"date": date.today().isoformat(), "calls": 0}
        return data

    @property
    def calls_today(self) -> int:
        return self._load()["calls"]

    def register(self) -> None:
        data = self._load()
        if data["calls"] >= self.limit:
            raise DailyLimitError(
                f"Limite diário local atingido ({self.limit} chamadas). "
                "Ajuste DAILY_LIMIT no .env ou tente amanhã."
            )
        data["calls"] += 1
        _write_json(self.path, data)
