"""Configuração validada com pydantic-settings (lê variáveis de ambiente e o arquivo .env)."""
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# Modelos gratuitos com tool calling, em ordem de preferência (fallback automático para o próximo).
# Confira a lista atual em https://openrouter.ai/models e ajuste OPENROUTER_MODELS no .env.
_DEFAULT_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # NoDecode: aceita lista separada por vírgulas no .env (em vez de JSON)
    models: Annotated[list[str], NoDecode] = Field(default=_DEFAULT_MODELS, validation_alias="OPENROUTER_MODELS")

    db_path: Path = Field(default=Path("cinerocket.db"), validation_alias="CINEROCKET_DB")

    max_rows: int = Field(50, ge=1, le=1000)           # linhas devolvidas ao modelo por consulta
    query_timeout_s: float = Field(120, gt=0)          # margem para máquinas mais lentas (junções grandes)
    max_steps: int = Field(5, ge=1, le=15)             # iterações máximas do laço de tool calling
    daily_limit: int = Field(45, ge=1)                 # trava local (conta gratuita: 50 req/dia)
    llm_timeout_s: float = Field(60, gt=0)

    data_dir: Path = ROOT / "data"

    @field_validator("models", mode="before")
    @classmethod
    def _split_models(cls, v):
        if isinstance(v, str):
            v = [m.strip() for m in v.split(",")]
        v = [m for m in v if m]
        if not v:
            raise ValueError("OPENROUTER_MODELS não pode ficar vazio")
        return v

    @field_validator("db_path", mode="after")
    @classmethod
    def _resolve_db_path(cls, v: Path) -> Path:
        return v if v.is_absolute() else ROOT / v

    @property
    def cache_path(self) -> Path:
        return self.data_dir / "cache.json"

    @property
    def usage_path(self) -> Path:
        return self.data_dir / "usage.json"


settings = Settings()
