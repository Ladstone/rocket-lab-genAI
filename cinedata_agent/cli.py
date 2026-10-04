"""Interface de linha de comando: pergunta única ou conversa interativa."""
import argparse
import sys

from .agent import CineDataAgent, LLMUnavailable
from .config import settings
from .storage import DailyLimitError


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return "" if v is None else str(v)


def _print_table(query, max_rows: int = 15) -> None:
    """Mostra o resultado real do banco, independentemente do texto gerado pelo modelo."""
    rows = [[_fmt(v) for v in r] for r in query.rows[:max_rows]]
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(query.columns)]
    print("\nDados retornados:")
    print("  " + " | ".join(c.ljust(w) for c, w in zip(query.columns, widths)))
    print("  " + "-+-".join("-" * w for w in widths))
    for r in rows:
        print("  " + " | ".join(v.ljust(w) for v, w in zip(r, widths)))
    if len(query.rows) > max_rows or query.truncated:
        print(f"  … (mostrando {len(rows)} linhas)")


def _print_result(res, show_sql: bool) -> None:
    tag = " (cache)" if res.from_cache else f" [{res.model}, {res.llm_calls} chamada(s)]"
    print(f"\n{res.answer}")
    if res.successful_queries:
        _print_table(res.successful_queries[-1])
    print(f"\n—{tag}")
    if show_sql:
        for i, q in enumerate(res.queries, 1):
            status = f"erro: {q.error}" if q.error else f"{len(q.rows)} linha(s)"
            print(f"\n[SQL {i}] {status}\n{q.sql}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="cinedata_agent", description="Pergunte ao catálogo CineData em linguagem natural.")
    p.add_argument("pergunta", nargs="?", help="Pergunta única. Sem ela, abre o modo interativo.")
    p.add_argument("--sql", action="store_true", help="Mostra os SQLs executados.")
    p.add_argument("--memoria", action="store_true", help="Mantém o contexto entre perguntas (desativa o cache).")
    p.add_argument("--sem-cache", action="store_true", help="Ignora o cache de respostas.")
    args = p.parse_args(argv)

    if not settings.openrouter_api_key:
        sys.exit("Defina OPENROUTER_API_KEY no arquivo .env (veja .env.example).")
    if not settings.db_path.exists():
        sys.exit(f"Banco não encontrado em {settings.db_path}. Veja o README (passo 3).")

    agent = CineDataAgent()

    def ask(q: str) -> None:
        try:
            _print_result(agent.ask(q, use_cache=not args.sem_cache, use_memory=args.memoria), args.sql)
        except (LLMUnavailable, DailyLimitError) as exc:
            print(f"\n⚠️  {exc}")

    if args.pergunta:
        ask(args.pergunta)
        return

    print(f"CineData Agent — modelos: {', '.join(agent.models)}")
    print(f"Chamadas hoje: {agent.usage.calls_today}/{agent.usage.limit}. Digite 'sair' para encerrar.")
    while True:
        try:
            q = input("\nPergunta> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in {"sair", "exit", "quit"}:
            break
        if q:
            ask(q)


if __name__ == "__main__":
    main()
