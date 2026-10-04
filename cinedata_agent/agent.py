"""Agente Text-to-SQL: laço de tool calling com OpenRouter, fallback entre modelos e cache."""
import json

from openai import OpenAI
from pydantic import ValidationError

from .config import settings
from .db import QueryError, execute_readonly
from .prompts import build_system_prompt
from .schemas import AgentResult, CacheEntry, QueryRecord, RunSqlArgs, run_sql_parameters
from .storage import DailyLimitError, ResponseCache, UsageTracker

TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Executa uma consulta SELECT (SQLite) na camada Gold e devolve colunas e linhas.",
        "parameters": run_sql_parameters(),
    },
}]


def answer_uses_data(answer: str, queries: list[QueryRecord]) -> bool:
    """Heurística: a resposta cita ao menos um texto (ex.: título) das primeiras linhas do resultado?"""
    ok = [q for q in queries if q.ok]
    if not ok:
        return False
    texts = [v for row in ok[-1].rows[:3] for v in row if isinstance(v, str) and len(v) >= 3]
    return not texts or any(t.lower() in answer.lower() for t in texts)


class LLMUnavailable(Exception):
    """Todos os modelos configurados falharam."""


class CineDataAgent:
    def __init__(self, client=None, models=None, cache=None, usage=None,
                 db_path=None, max_steps=None, memory_turns=3):
        self.client = client or OpenAI(base_url=settings.openrouter_base_url,
                                       api_key=settings.openrouter_api_key or "sem-chave")
        self.models = list(models or settings.models)
        self.cache = cache if cache is not None else ResponseCache()
        self.usage = usage if usage is not None else UsageTracker()
        self.db_path = db_path or settings.db_path
        self.max_steps = max_steps or settings.max_steps
        self.memory_turns = memory_turns
        self.history: list[tuple[str, str]] = []  # (pergunta, resposta) para memória de conversa

    # ------------------------------------------------------------------ LLM
    def _chat(self, messages: list[dict]):
        """Tenta cada modelo em ordem; o primeiro que responder vence (fallback)."""
        errors = []
        for model in list(self.models):
            try:
                self.usage.register()
                resp = self.client.chat.completions.create(
                    model=model, messages=messages, tools=TOOLS,
                    temperature=0, timeout=settings.llm_timeout_s,
                )
                if not resp.choices:  # OpenRouter às vezes devolve erro com HTTP 200
                    raise ValueError("resposta sem 'choices'")
                self.models.remove(model)   # o que funcionou vira o primeiro da fila:
                self.models.insert(0, model)    # não gasta cota tentando de novo o que acabou de falhar
                return model, resp.choices[0].message
            except DailyLimitError:
                raise
            except Exception as exc:  # 429, 5xx, timeout, modelo sem tool calling...
                errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:120]}")
        raise LLMUnavailable("Nenhum modelo respondeu.\n" + "\n".join(errors))

    # ----------------------------------------------------------------- tools
    def _run_tool(self, call, queries: list[QueryRecord]) -> str:
        try:
            args = RunSqlArgs.model_validate_json(call.function.arguments or "{}")
        except ValidationError:
            return json.dumps({"erro": "Argumentos inválidos. Envie {\"query\": \"SELECT ...\"}."})
        try:
            result = execute_readonly(args.query, db_path=self.db_path)
            queries.append(QueryRecord(sql=args.query, **result))
            return json.dumps(result, ensure_ascii=False, default=str)
        except QueryError as exc:
            queries.append(QueryRecord(sql=args.query, error=str(exc)))
            return json.dumps({"erro": str(exc)}, ensure_ascii=False)

    # ------------------------------------------------------------------- API
    def ask(self, question: str, use_cache: bool = True, use_memory: bool = False) -> AgentResult:
        # Cache só vale para perguntas independentes (com memória, o contexto muda a resposta).
        if use_cache and not use_memory:
            hit = self.cache.get(question)
            if hit:
                return AgentResult(answer=hit.answer, queries=hit.queries, model=hit.model, from_cache=True)

        messages = [{"role": "system", "content": build_system_prompt()}]
        if use_memory:
            for q, a in self.history[-self.memory_turns:]:
                messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        messages.append({"role": "user", "content": question})

        queries: list[QueryRecord] = []
        llm_calls, used_model, answer = 0, None, None
        for _ in range(self.max_steps):
            used_model, msg = self._chat(messages)
            llm_calls += 1
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                answer = (msg.content or "").strip() or "Não consegui gerar uma resposta."
                break
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name, "arguments": c.function.arguments}}
                               for c in tool_calls],
            })
            for call in tool_calls:
                content = self._run_tool(call, queries) if call.function.name == "run_sql" \
                    else json.dumps({"erro": f"Ferramenta desconhecida: {call.function.name}"})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

        if answer is None:
            answer = "Não consegui concluir a análise dentro do limite de passos. Tente reformular a pergunta."
        result = AgentResult(answer=answer, queries=queries, model=used_model, llm_calls=llm_calls)

        if use_memory:
            self.history.append((question, answer))
        if use_cache and not use_memory and answer_uses_data(answer, queries):
            self.cache.set(question, CacheEntry(answer=answer, queries=queries, model=used_model))
        return result
