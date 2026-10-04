# CineData Agent — Text-to-SQL sobre a camada Gold

Agente em Python que permite a usuários não técnicos fazer perguntas em linguagem natural sobre o
catálogo de filmes da CineData Analytics. O modelo (via OpenRouter, com *tool calling*) gera SQL,
o agente executa **somente leitura** no `cinerocket.db` (SQLite, camada Gold) e a resposta volta em português.

> **Escolha de framework:** o agente usa o SDK da OpenAI diretamente (compatível com o OpenRouter),
> sem LangChain ou similares. Foi uma decisão deliberada: menos dependências, controle total do laço
> de tool calling e menos chamadas gastas na cota gratuita.

```
pergunta ──► LLM ──(tool call: run_sql)──► guardrails ──► SQLite (read-only) ──► LLM ──► resposta
               ▲                                                                   │
               └────────── erro de SQL volta ao modelo para autocorreção ◄─────────┘
```

## Passo a passo para executar

**Pré-requisito:** Python 3.10 ou mais novo.

1. **Clonar e entrar na pasta**
```bash
   git clone https://github.com/Ladstone/rocket-lab-genAI
   cd rocket-lab-genAI
```
2. **Criar o ambiente virtual e instalar as dependências**
```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows (PowerShell): .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
```
3. **Colocar o banco na raiz do projeto** — baixe o `cinerocket.db` na pasta compartilhada da atividade
   (se vier como `cinerocket (1).db`, renomeie para `cinerocket.db`). Ele não é versionado
   (≈580 MB, acima do limite do GitHub).
4. **Configurar a chave do OpenRouter**
```bash
   cp .env.example .env             # Windows: copy .env.example .env
```
   Edite o `.env` e preencha `OPENROUTER_API_KEY` (gere em https://openrouter.ai/keys).
   Confira em https://openrouter.ai/models se os modelos de `OPENROUTER_MODELS` ainda existem
   e têm suporte a *tools*; a lista é tentada em ordem (fallback automático).
5. **Rodar**
```bash
   python -m cinedata_agent "Quais os 5 filmes mais populares?" --sql   # pergunta única, mostrando o SQL
   python -m cinedata_agent                                            # modo interativo
   python -m cinedata_agent --memoria                                  # interativo com memória de conversa
```
   Além do texto gerado pelo modelo, o terminal sempre imprime a tabela real retornada pelo banco
   ("Dados retornados"), para conferência.

## Limite de 50 requisições/dia (conta gratuita)

- **Cache de respostas** (`data/cache.json`): perguntas repetidas não gastam cota. Use `--sem-cache` para ignorar.
- **Contador diário local** (`data/usage.json`): bloqueia ao atingir `DAILY_LIMIT` (padrão 45).
- **Fallback entre modelos**: se um modelo falha, tenta o próximo; o que funcionou passa a ser o primeiro da fila.
- Cada pergunta usa em geral 2 chamadas (gerar SQL → redigir resposta); erros de SQL custam mais 1 por tentativa.
- **Antes de gastar cota**, valide tudo sem LLM: `python -m pytest` e `python -m eval.run_eval --sql-only`.

## Pydantic

- **`config.py`**: `Settings` (pydantic-settings) valida o `.env` na inicialização — lista de modelos separada por vírgula, limites numéricos, caminho do banco. Configuração inválida falha na hora, com mensagem clara.
- **`schemas.py`**: `RunSqlArgs` valida os argumentos que o LLM envia à ferramenta (e gera o JSON Schema da tool); `QueryRecord`/`AgentResult` tipam a saída do agente; `CacheEntry` descarta entradas de cache corrompidas; `EvalQuestion` valida o arquivo de avaliação.

## Guardrails

| Camada | O que faz |
|---|---|
| Validação textual | Só `SELECT`/`WITH`, uma única instrução |
| Conexão `mode=ro` | O SQLite recusa qualquer escrita |
| `set_authorizer` | Nega `PRAGMA`, `ATTACH`, DDL/DML mesmo que passem pela validação |
| Limites | 50 linhas por consulta, textos longos cortados, timeout de 120 s |
| Prompt | Recusa assuntos fora do catálogo e pedidos de alteração de dados |

## Regras de negócio embutidas no prompt (descobertas no banco)

- `receita_*` e `orcamento_*` são `NULL` quando não informados (só ~3,5% dos filmes têm receita);
  `lucro_*` converte `NULL` em 0, então só vale com receita informada.
- Notas com valor `0` significam "sem nota" (filtradas com `> 0`).
- **Margem de lucro** = `lucro / receita × 100` (em %). Para grupos (gênero, produtora) usa-se a **margem agregada**
  `SUM(lucro) / SUM(receita)`, porque a média das margens por filme é dominada por outliers.
- `tipo_pessoa` ∈ Ator / Diretor / Roteirista; "últimos 5 anos" considera `status_filme = 'Lançado'`.
- Existem títulos duplicados com `sk_movie_id` diferentes; as agregações são feitas por `sk_*`.
- Consultas que cruzam atores × diretores são pesadas (745 mil linhas em `bridge_movie_person`); o prompt traz
  um exemplo otimizado (pré-filtro de pessoas com muitos filmes) que reduz o tempo de ~1 min para ~5 s.

## Observações sobre a qualidade dos dados

- Alguns filmes têm `popularidade` com valores que parecem anos (2018, 2019, 2020), por isso aparecem no topo
  do ranking de popularidade. O agente apenas reproduz o que está na camada Gold.
- Receita e orçamento existem para uma pequena parcela do catálogo, então as análises financeiras
  consideram só esses filmes.

## Avaliação

`eval/questions.json` traz 14 perguntas (as 5 categorias do enunciado) com SQL de referência.

```bash
python -m eval.run_eval --sql-only       # roda só a referência, sem LLM (~20 s)
python -m eval.run_eval --limit 3        # roda o agente em 3 perguntas e compara com a referência
python -m eval.run_eval --id q09         # uma pergunta específica (--no-cache para refazer)
```

A comparação é heurística: a linha de referência deve estar contida no resultado do agente (com tolerância
numérica de 0,1%); em rankings com empates compara só as métricas. Quando uma pergunta falha, o script imprime
o SQL do agente ao lado da referência. O relatório fica em `eval/results.json`.

## Estrutura

```
cinedata_agent/
  agent.py     laço de tool calling, fallback entre modelos, memória e cache
  db.py        execução SQL segura (read-only, authorizer, limites, timeout)
  prompts.py   esquema da camada Gold + regras de negócio + prompt de sistema
  storage.py   cache de respostas e contador diário de chamadas
  config.py    configurações validadas (pydantic-settings, lê o .env)
  schemas.py   modelos pydantic: argumentos da tool, QueryRecord, AgentResult, cache, avaliação
  cli.py       interface de linha de comando
eval/          perguntas com SQL de referência + script de avaliação
tests/         testes (guardrails, agente com LLM falso, esquema vs. banco real)
```

## Testes

```bash
python -m pytest -q    # 51 testes, não chamam a API
```