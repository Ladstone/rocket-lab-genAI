"""Avaliação do agente contra um conjunto de perguntas com SQL de referência.

Modos:
  python -m eval.run_eval --sql-only          # roda só os SQLs de referência (não gasta cota do LLM)
  python -m eval.run_eval --limit 3           # roda o agente em 3 perguntas e compara com a referência
  python -m eval.run_eval --id q02 --id q03   # perguntas específicas (use --no-cache para refazer)
"""
import argparse
import json
import math
import re
import time
from pathlib import Path

from cinedata_agent.agent import CineDataAgent, LLMUnavailable
from cinedata_agent.config import settings
from cinedata_agent.db import QueryError, execute_readonly
from cinedata_agent.schemas import EvalQuestion
from cinedata_agent.storage import DailyLimitError

QUESTIONS = Path(__file__).parent / "questions.json"
RESULTS = Path(__file__).parent / "results.json"


_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _to_num(v) -> float | None:
    """Converte número ou texto numérico ('100.00%', 'R$ 1.234,50') em float; None se não for número."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace("%", "").replace("R$", "").replace("US$", "").replace(" ", "")
    if "," in t:  # formato brasileiro: 1.234,56
        t = t.replace(".", "").replace(",", ".")
    return float(t) if _NUM_RE.match(t) else None


def _close(a, b) -> bool:
    """Números (inclusive em texto): tolerância relativa de 0,1%. Textos: sem caixa/espaços."""
    na, nb = _to_num(a), _to_num(b)
    if na is not None and nb is not None:
        return math.isclose(na, nb, rel_tol=1e-3, abs_tol=0.01)
    return str(a).strip().casefold() == str(b).strip().casefold()


def _row_contains(ref_row, agent_row) -> bool:
    return all(any(_close(r, a) for a in agent_row) for r in ref_row)


def compare(ref_rows, agent_rows, mode=None) -> bool:
    """Cada linha de referência precisa aparecer no resultado do agente.

    Padrão: os valores da linha de referência devem estar contidos em alguma linha do agente
    (o agente pode trazer colunas extras ou mais linhas). Modo 'metric': compara só as métricas
    numéricas na mesma posição do ranking — útil quando há empates e títulos diferentes são válidos.
    """
    if len(agent_rows) < len(ref_rows):
        return False
    if mode == "metric":
        return all(
            _row_contains([n for n in map(_to_num, r) if n is not None],
                          [n for n in map(_to_num, a) if n is not None])
            for r, a in zip(ref_rows, agent_rows)
        )
    return all(any(_row_contains(r, a) for a in agent_rows) for r in ref_rows)


def _show_failure(q, ref, res, ok_queries) -> None:
    print("    --- diagnóstico ---")
    if not ok_queries:
        errors = [x.error for x in res.queries if x.error]
        print(f"    nenhum SQL bem-sucedido. Erros: {errors or 'o modelo não chamou run_sql'}")
        print(f"    resposta: {res.answer[:200]}")
        return
    last = ok_queries[-1]
    print(f"    SQL do agente : {' '.join(last.sql.split())[:300]}")
    print(f"    agente ({len(last.rows)} linhas): {last.rows[:3]}")
    print(f"    referência ({len(ref['rows'])} linhas): {ref['rows'][:3]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql-only", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--id", action="append", dest="ids")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    questions = [EvalQuestion(**q) for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))]
    if args.ids:
        questions = [q for q in questions if q.id in args.ids]
    if args.limit:
        questions = questions[: args.limit]

    agent = None
    if not args.sql_only:
        agent = CineDataAgent()

    report, passed = [], 0
    for q in questions:
        t0 = time.time()
        try:
            ref = execute_readonly(q.reference_sql, timeout_s=settings.query_timeout_s)
        except QueryError as exc:
            print(f"[{q.id}] ERRO na referência: {exc}")
            continue
        entry = {"id": q.id, "question": q.question, "reference_rows": ref["rows"][:3]}
        if args.sql_only:
            print(f"[{q.id}] {q.question}  ({time.time() - t0:.1f}s)\n    {ref['rows'][:3]}")
        else:
            try:
                res = agent.ask(q.question, use_cache=not args.no_cache)
            except (DailyLimitError, LLMUnavailable) as exc:
                print(f"[{q.id}] PARADO: {exc}")
                print("    As perguntas já respondidas ficam no cache: rode de novo depois, sem gastar cota delas.")
                break
            ok_queries = res.successful_queries
            ok = bool(ok_queries) and compare(ref["rows"], ok_queries[-1].rows, q.match)
            passed += ok
            entry.update(passed=ok, model=res.model, from_cache=res.from_cache,
                         agent_sql=ok_queries[-1].sql if ok_queries else None, answer=res.answer)
            print(f"[{q.id}] {'OK  ' if ok else 'FALHA'} {q.question}"
                  f"{' (cache)' if res.from_cache else ''}")
            if not ok:
                _show_failure(q, ref, res, ok_queries)
        report.append(entry)

    if not args.sql_only:
        print(f"\nAcertos: {passed}/{len(report)}")
        RESULTS.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"Relatório salvo em {RESULTS}")


if __name__ == "__main__":
    main()
