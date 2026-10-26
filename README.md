# IT/DevOps Troubleshooting Copilot — Agentic RAG

An **agentic RAG** system that helps engineers diagnose real infrastructure problems. Paste an error, a pod status or raw `kubectl` output and the agent **triages the logs, searches runbooks and docs, calls tools, and returns a ranked diagnosis with safe, risk-labelled commands** — grounded in retrieved evidence, verified, cached, and fully traced.

> **Core idea:** don't just build a RAG system — build an *agent* you can measure, debug and trust. It never executes commands; it suggests them and tells you which ones change state.

**Try it** (once running): *"My api pod keeps restarting — Last State: OOMKilled, Exit Code 137, limit 256Mi"* → the agent parses the log, finds the OOMKilled signature, retrieves the memory-limits runbook, and answers with *Diagnosis → Likely causes → Fix steps → Commands → Confidence*, citing its sources.

---

## 1. What This Project Demonstrates

| Capability | How |
| --- | --- |
| **Agentic loop (ReAct)** | LangGraph agent decides to *search again*, *call a tool* or *answer*, with a step budget and loop protection |
| **Tool use** | 8 deterministic tools: log triage, Kubernetes status/exit-code playbook, CPU/memory fit, unit conversion, CIDR maths + overlap, port lookup, command safety |
| **Grounded answers** | Strict evidence-only prompt, citations, and an LLM **groundedness check with one regeneration** |
| **Caching** | Redis **exact + semantic answer cache**, **retrieval cache**, auto-invalidated when the corpus changes |
| **Observability** | Logfire (app spans) + **Langfuse** (LLM traces, tokens, scores, user feedback) + `/stats` (P50/P95/P99, cache hit-rate) |
| **Safety** | Input guardrails (fail-closed), **output command-risk review**, prompt-injection delimiters, API key + rate limit |
| **Cost control** | Model tiering (fast model for routing/verification, strong model for the answer), caches, token accounting |
| **Evaluation** | RAGAS on a golden set **plus** 12 behavioural scenarios with a pass-rate gate |
| **Scalability** | Stateless API (history in Redis), Docker Compose, CI |

| Architecture overview | Chat UI | Evaluation UI |
| --- | --- | --- |
| ![Architecture overview](DOCS/assets/architecture-overview.png) | ![Chat UI with sources](DOCS/assets/ui-chat.png) | ![Evaluation dashboard](DOCS/assets/evaluation-ui.png) |

_(Screenshots are from the first version of the project; the chat UI now also shows agent steps, tool badges, cache status, command-safety review and 👍/👎 feedback.)_

---

## 2. Main Components

| Layer | Technology | Purpose |
| --- | --- | --- |
| UI | Streamlit | Chat, agent trace, command-risk badges, feedback |
| API | FastAPI | `/query`, `/feedback`, `/stats`, `/health` |
| Agent | LangGraph | Planner → search/tool loop → answer → verify → finalize |
| Tools | Plain Python (`app/tools/`) | Log triage, K8s playbook, resources, CIDR, ports, command safety |
| Vector DB | Qdrant | Semantic retrieval |
| Embeddings | Sentence Transformers (`all-mpnet-base-v2`) | One provider for ingest **and** query |
| Reranker | FlashRank | Cross-encoder refinement |
| Cache + memory | Redis (memory fallback) | Answer cache, retrieval cache, conversation history |
| Guardrails | NeMo + deterministic checks | Input safety (fail-closed) |
| LLM gateway | Portkey | Provider abstraction, retries, fallback |
| Observability | Logfire · Langfuse · `/stats` | Spans · LLM traces/scores · runtime metrics |
| Evaluation | RAGAS + scenario evals | Retrieval/answer quality · agent behaviour |

---

## 3. End-to-End Flow

```
User message
   │
   ▼
Input guardrails ──blocked──► safe refusal
   │
   ▼
Answer cache  (only for standalone questions: exact → semantic) ──hit──► cached answer (~ms, no LLM)
   │ miss
   ▼
LangGraph agent  ───────────────────────────────────────────────────────────────┐
   planner ─ conversational ─────────────────────────────► respond              │
      │  (pasted logs → parse_logs runs automatically)                          │
      ▼                                                                         │
   search (Qdrant top-15 → FlashRank top-5, retrieval-cached)                   │
      ▼                                                                         │
   agent ◄───────────┐   decides: search again │ call a tool │ final             │
      ├─ tool ──► tools ┘   (max AGENT_MAX_STEPS, duplicate calls blocked)       │
      └─ final ─► respond (strong model, evidence-only, structured)             │
                     ▼                                                          │
                  verify (fast model: unsupported claims?) ─ yes ─► regenerate once
                     ▼                                                          │
                  finalize: command-safety review + caveat if still unverified ─┘
   │
   ▼
Answer + citations + tools used + agent trace + command risks  →  cached if grounded
```

Conversation history is stored in Redis (`thread_id`), so the API is **stateless** and any replica can serve any turn.

---

## 4. Document Ingestion

Supported formats:

- PDF
- HTML
- TXT
- DOCX
- PPTX

```text
Source
  ↓
Parser
  ↓
Text Extraction
  ↓
Chunking
  ↓
Embeddings
  ↓
Qdrant
```

Current retrieval embedding:

`sentence-transformers/all-mpnet-base-v2`

Vector dimension: **768**

---

### Runbook corpus

`DATA/runbooks/` ships 9 starter runbooks (CrashLoopBackOff, OOMKilled, ImagePullBackOff, Pending/scheduling, service & DNS connectivity, node NotReady, MTU/packet loss, NUMA performance, TLS/certificates). They are **written from general operational practice — verify against your platform version** — and exist so the agent works out of the box. Replace or extend them with your own runbooks, postmortems and vendor docs (`.txt/.pdf/.html/.docx/.pptx`), then re-ingest. Re-ingesting bumps a *corpus version* that invalidates every cached answer and retrieval automatically.

---
## 5. Retrieval + Reranking

The system uses a two-stage retrieval strategy:

```text
Query
  ↓
Qdrant
  ↓
Top 15 candidates
  ↓
FlashRank
  ↓
Top 5 relevant chunks
```

Why?

- Vector search → fast candidate discovery
- Cross-encoder → better relevance ordering

If reranking fails, the system can fall back to the original retrieval results.

---

## 6. The Agent

The agent is a **ReAct-style loop** implemented as a LangGraph state machine (`app/agents/`).

| Node | Model tier | Job |
| --- | --- | --- |
| `planner` | fast | Classify (`conversational` / `question` / `troubleshoot`), write a standalone search query; auto-runs `parse_logs` if the input looks like logs |
| `search` | — | Qdrant → rerank (cached); merges chunks into the evidence pool |
| `agent` | fast | Chooses `search` / `tool` / `final` as strict JSON; garbage output safely falls back to answering |
| `tools` | — | Runs a tool and appends the observation |
| `respond` | strong | Writes the answer using **only** the evidence (documents + tool results) |
| `verify` | fast | Groundedness check; one regeneration with the reviewer's feedback |
| `finalize` | — | Command-safety review + caveat if still unverified |

Termination is guaranteed: a step budget (`AGENT_MAX_STEPS`), duplicate search/tool blocking, and a LangGraph recursion limit.

### Tools

| Tool | What it does |
| --- | --- |
| `parse_logs` | Detects ~20 failure signatures (CrashLoopBackOff, OOMKilled, DNS, TLS, timeouts…), entities (pods, IPs, exit codes) and suggests searches |
| `explain_k8s_status` | Built-in playbook for pod statuses / event reasons / exit codes (137, 139, 143…) |
| `check_resource_fit` | Do a pod's CPU/memory *requests* fit a node's allocatable resources? |
| `convert_k8s_units` | `250m`, `512Mi`, `2Gi` → millicores / bytes |
| `cidr_info`, `cidr_overlap` | Subnet maths; do pod/service/VPN ranges overlap? |
| `port_info` | What usually listens on a port (6443, 2379, 10250, NodePort range…) |
| `check_command` | Classifies a shell command: `read_only` / `mutating` / `destructive` / `unknown` |

Tools are deterministic and unit-tested; the LLM only decides *when* to use them. Add a tool by registering it in `app/tools/registry.py` — it is advertised to the agent automatically.

### Safety

* **The assistant never executes anything.** It suggests commands; every fenced/inline command is classified and a ⚠️ **Command safety review** is appended if any change state.
* Logs, documents and tool output are wrapped in delimiters and treated as **data, never instructions** (prompt-injection defence — see the `prompt-injection-in-logs` scenario).

---

## 7. Guardrails

Two control layers:

```text
User Input
    ↓
Deterministic Checks
    ↓
NeMo Guardrails
    ↓
Allow / Block
```

A blocked request exits before the RAG pipeline.

Important production lesson:

> **A successful LLM call does not necessarily mean a successful application decision.**

Machine-level guardrail decisions should be validated and unexpected outputs should fail closed.

---

## 8. LLM Gateway

The application uses Portkey as an OpenAI-compatible gateway:

```text
Application
    ↓
OpenAI-compatible Client
    ↓
Portkey
    ↓
Saved Provider / Integration
    ↓
Model
```

This provides a separation between application logic and model providers.

Gateway capabilities can include:

- Routing
- Retries
- Fallbacks
- Caching
- Metadata
- Centralized controls

---

## 9. Observability

Three complementary layers — each is optional-safe (failures never break a request):

| Layer | Answers | Where |
| --- | --- | --- |
| **Logfire** | What did the *application* do? Spans for every node, retrieval, LLM call (latency, tokens) | `LOGFIRE_TOKEN` |
| **Langfuse** | What did the *LLM* see and say? Full trace per request, prompts/completions, token usage, scores (`grounded`, `agent_steps`, `user_feedback`), sessions per `thread_id` | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` |
| **`/stats`** | How is the *system* doing right now? P50/P95/P99 latency, cache hit-rate, tool usage, avg agent steps, verify failures, token totals per tier | `GET /stats` |

Langfuse works with [Langfuse Cloud](https://langfuse.com) or a self-hosted instance (use Langfuse's own docker-compose). Every `/query` response includes `trace_id`, so a 👎 from the UI is attached to the exact trace via `POST /feedback` — turning user complaints into debuggable traces.

Example `/stats`:

```json
{"requests": 120, "latency_ms": {"p50": 41.0, "p95": 9200.0, "p99": 14100.0},
 "cache": {"exact_hits": 30, "semantic_hits": 18, "misses": 72, "hit_rate": 0.4},
 "agent": {"avg_steps": 2.1, "tool_calls": {"parse_logs": 44, "cidr_overlap": 3}, "verify_failed": 5},
 "llm": {"calls": {"fast": 310, "answer": 72}, "tokens": {"prompt": 410233, "completion": 61802}}}
```
_(illustrative values — your numbers will differ.)_

---

## 10. Evaluation

The project uses a repeatable golden dataset and evaluates the live pipeline.

### RAG metrics

| Metric | Measures |
|---|---|
| Faithfulness | Is the answer supported by context? |
| Answer Relevancy | Does the answer address the question? |
| Context Precision | Are relevant chunks ranked higher? |
| Context Recall | Did retrieval find the required evidence? |
| Answer Correctness | How close is the answer to the reference? |
| Tool Correctness | Was the expected route/tool selected? |

### Guardrail metrics

`Precision · Recall · Accuracy`

### Agent behaviour scenarios (new)

RAGAS measures *answer quality*; `evals/scenarios.json` measures *agent behaviour* — 12 scenarios checking that the agent uses the right tool (`parse_logs`, `cidr_overlap`, `explain_k8s_status`…), mentions the right fix, cites the right runbook, flags risky commands, resists prompt injection hidden in logs, blocks jailbreaks, and serves repeated questions from cache.

```bash
python -m evals.scenario_eval        # exits non-zero if pass-rate < SCENARIO_MIN_PASS_RATE (default 0.8)
```

The check logic is a pure function with unit tests; the runner needs a live stack (also available as a manual GitHub Actions job).

---
## 11. Baseline: Failure as a Diagnostic Signal

One observed baseline:

```text
Faithfulness        0.089
Answer Relevancy    0.885
Context Precision   0.214
Context Recall     0.095
Answer Correctness  0.607
Tool Correctness   1.000
```

The important observation is not the individual number.

It is the pattern:

```text
High answer relevance
        +
Low retrieval quality
        +
Low faithfulness
```

This points investigation toward:

**Parsing → Chunking → Embeddings → Retrieval → Reranking → Context**

before simply changing the LLM.

> These are experimental baseline results, not production guarantees.

**Likely root causes found in the code review (and fixed — see section 21):** ingestion and query-time retrieval could use *different* embedding models (3072-dim Gemini vs 768-dim local), the chunker produced oversized chunks (up to ~3,000 chars vs. the embedding model's ~384-token window) because PDF text has no blank lines, and the answer prompt never told the LLM to stay within the retrieved context. Re-run the eval to get post-fix numbers (table below).

| Metric | Baseline (before fixes) | After fixes |
| --- | --- | --- |
| Faithfulness | 0.089 | _run `streamlit run evals/app.py` and fill in_ |
| Answer Relevancy | 0.885 | _…_ |
| Context Precision | 0.214 | _…_ |
| Context Recall | 0.095 | _…_ |
| Answer Correctness | 0.607 | _…_ |
| Tool Correctness | 1.000 | _…_ |

---

## 12. Evaluation Workflow

```text
Golden Dataset
      ↓
Run Live Pipeline
      ↓
Capture Responses + Context
      ↓
RAGAS / Guardrail Metrics
      ↓
Analyze
      ↓
Change One Variable
      ↓
Re-run
```

This turns:

> “This version feels better.”

into:

> “This version changed measurable system behavior.”

---

## 13. Experimentation

Experiment dimensions include:

- Chunk size / strategy
- Embedding model
- Retrieval top-K
- Reranking
- Prompt
- LLM
- Context limits
- Routing
- Guardrail strategy

Use the **same golden dataset** when comparing changes.

---

## 14. Performance & Reliability

* **Three cache layers** (Redis): exact answers, semantic answers (cosine ≥ `SEMANTIC_THRESHOLD`, numbers must match so *port 6443* never hits *port 2379*), and retrieval results. All namespaced by prompt version + corpus version + model. A cache or Redis failure never fails a request (falls back to a per-process memory store).
* Only **standalone, grounded** answers are cached — never history-dependent turns, conversational replies, ungrounded or "not found" answers.
* **Model tiering**: routing, tool decisions and verification use `LLM_FAST_MODEL`; only the final answer uses the strong model.
* **Bounded work**: step budget, duplicate-call blocking, bounded context, batch embeddings, lazy model init.
* **Fail-safe components**: reranker falls back to vector order; verifier failure is skipped (not blocking); guardrails fail closed.
* **Cost/latency trade-off to know about**: a cache *miss* on a technical question makes roughly 4–6 LLM calls (planner, 0–3 agent decisions, answer, verify). Set `ENABLE_VERIFY=false` or lower `AGENT_MAX_STEPS` to trade rigor for speed; the caches make repeats nearly free.

---
## 15. Project Structure

```
app/
├── agents/            # LangGraph: state, graph, nodes (planner, search, agent, tools, responder, verify, finalize)
├── tools/             # log_parser, k8s_playbook, network_tools, resource_tools, command_safety, registry
├── cache/             # store (Redis|memory), answer_cache (exact+semantic), retrieval_cache, conversation
├── observability/     # langfuse_setup, stats
├── ingestion/         # loaders (pdf/html/txt/office), recursive chunker, processor
├── services/retrieval/# embeddings (single provider), qdrant_service, ranking_service
├── guardrails/        # deterministic checks + NeMo (fail-closed)
├── gateway/           # Portkey / OpenAI-compatible client
├── llm.py, llm_json.py# single LLM entry point (tiers, tokens, latency) + robust JSON parsing
├── main.py            # FastAPI: /query /feedback /stats /health /conversations
└── config.py          # env-driven settings

evals/                 # golden dataset + RAGAS pipeline + guardrail eval + scenarios.json / scenario_eval.py
ui/app.py              # Streamlit chat UI
tests/                 # 140+ pytest tests (fake LLM, fakeredis; no network / API keys)
DATA/                  # sample corpus (true_data / noisy_data) + runbooks/
DOCS/                  # design docs + assets/
Dockerfile, docker-compose.yml (qdrant+redis+api+ui), .env.example, requirements.in/.txt, .github/workflows/ci.yml
```

---
## 16. Run Locally

### Option A — Docker (Qdrant + Redis + API + UI)

```bash
cp .env.example .env            # set PORTKEY_API_KEY + slugs (Qdrant/Redis URLs are preset by compose)
docker compose up --build
docker compose run --rm api python -m app.ingestion.processor DATA/runbooks --wipe   # runbooks only (fast)
# or the whole corpus:  ... processor DATA --wipe
# API docs: http://localhost:8000/docs   UI: http://localhost:8501   Metrics: http://localhost:8000/stats
```

### Option B — Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt           # pinned; edit requirements.in and re-compile to change
cp .env.example .env                      # then fill it in
docker run -p 6333:6333 qdrant/qdrant     # or Qdrant Cloud via QDRANT_* vars
docker run -p 6379:6379 redis:7-alpine    # optional; without REDIS_URL a per-process memory store is used

python -m app.ingestion.processor DATA/runbooks --wipe
uvicorn app.main:app --reload --port 8000
streamlit run ui/app.py                   # chat UI
streamlit run evals/app.py                # RAGAS evaluation UI
python -m evals.scenario_eval             # agent behaviour scenarios (API must be running)
```

> **One embedding provider for ingestion and querying** (`EMBEDDING_PROVIDER`, default `local`). Switching providers requires `--wipe`; the ingester refuses a dimension mismatch. When Redis is configured, run ingestion with the same `REDIS_URL` so the cache invalidation reaches the API.

### API

```bash
curl -s localhost:8000/query -H 'content-type: application/json' \
  -d '{"q":"Pod is CrashLoopBackOff. Last State: OOMKilled Exit Code: 137, limit 256Mi","thread_id":"demo"}' | jq
```
Response fields: `answer`, `citations`, `sources`, `tools_used`, `command_risks`, `thought_process` (agent trace), `steps`, `grounded`, `cache` (`miss|exact|semantic|bypass`), `latency_ms`, `trace_id`, `request_id`, `thread_id`.
`POST /feedback {"request_id","rating":"up|down","trace_id"}` · `GET /stats` · `DELETE /conversations/{thread_id}`.

### Configuration

Everything is env-driven (`app/config.py`, documented in `.env.example`): models and tiers, agent step budget, verification, cache TTLs/thresholds, chunking, retrieval sizes, `GUARDRAILS_FAIL_OPEN`, optional `API_KEY` (clients send `X-API-Key`), `RATE_LIMIT_PER_MINUTE`, `CORS_ORIGINS`, Langfuse keys.

### Tests & CI

```bash
pip install -r requirements-test.txt
pytest -q && ruff check app tests evals ui
```

GitHub Actions runs lint + unit tests and a Docker build on every push/PR (plus a manual scenario-eval job). Tests use a scripted fake LLM driving the **real** LangGraph, `fakeredis` for the Redis code paths, and stub retrieval — no API keys or network.

---
## 17. Deployment

`Dockerfile` (CPU-only torch, non-root user, healthcheck on `/health`) and `docker-compose.yml` are included. Still to do for a real production rollout: managed secrets, TLS / reverse proxy, persistent Qdrant with backups, load testing, and an eval regression gate in CI.

---

## 18. Continuous Improvement

```text
BUILD
  ↓
OBSERVE
  ↓
EVALUATE
  ↓
DIAGNOSE
  ↓
OPTIMIZE
  ↓
REPEAT
```

### Core lesson

**RAG quality is a system property.**

A stronger LLM cannot reliably compensate for poor retrieval.

A strong retriever cannot compensate for ungrounded generation.

The engineering task is to make every layer:

**measurable · explainable · reliable · improvable**

---

## 19. Limitations / Next Steps

**Honest status:** the code paths are unit-tested with fakes, but this version has **not yet been run against a live LLM / Qdrant / Redis / Langfuse** by the author of this change — run `python -m evals.scenario_eval` and the RAGAS evaluation first and treat those numbers as the real baseline. The built-in runbooks are starter content to be validated.

* Semantic cache scans vectors in-process (fine for ~thousands of entries); move to a vector index for millions
* Hybrid dense + sparse (BM25) retrieval, metadata filters, structure-aware chunking, OCR for scanned PDFs
* Streaming responses (currently the full answer is returned after verification)
* Real tool integrations behind explicit approval (read-only `kubectl`, Prometheus queries, log search) — today tools are pure functions and the agent never touches a cluster
* Cost-per-query in currency (tokens are tracked; pricing is model-specific), Prometheus `/metrics` export, alerting
* Auth beyond a shared API key (per-user keys / OIDC); rate limiting is per-instance (use a gateway or Redis for replicas)
* Larger golden/scenario sets, human-eval calibration, scheduled eval runs with regression gates in CI

---
## 20. Project Summary

**Problem:** engineers lose time triaging infrastructure incidents across logs, runbooks and tribal knowledge.
**Solution:** an agentic RAG copilot that triages logs, retrieves evidence, uses tools, and returns a grounded, cited, risk-labelled fix plan.
**Architecture:** FastAPI · LangGraph ReAct agent · Qdrant + FlashRank · Redis (exact/semantic/retrieval cache + memory) · Portkey gateway with model tiering · NeMo + deterministic guardrails · Logfire + Langfuse + `/stats` · RAGAS + scenario evals · Docker Compose + CI.

### Build → Measure → Learn → Improve

---
## 21. Changelog

### v2 — agentic troubleshooting copilot
Agent loop with tools, Redis caching (exact/semantic/retrieval), Langfuse + `/stats` observability, groundedness verification, command-safety review, stateless API with Redis memory, runbook corpus, scenario evals, Redis in Compose.

### v1.1 — review fixes

| Area | Problem | Fix |
| --- | --- | --- |
| Embeddings | Ingestion could use Gemini (3072-dim) while queries always used local (768-dim) → zero results, error swallowed | Single `embeddings.py`, explicit `EMBEDDING_PROVIDER`; ingest verifies collection dimension |
| Chunking | Split on `\n\n` only; PDF text has none → chunks up to ~3k chars, no overlap | Recursive splitter with overlap, text cleaning, hard size cap (tested) |
| Generation | Prompt didn't restrict the model to the context (hurts faithfulness) | Grounded prompt + explicit "not found" path (no LLM call when nothing retrieved) |
| Sources | Retriever dropped file names | Chunks carry `[Source: file]`; API returns `citations`; UI shows them |
| Guardrails | Failed open if NeMo errored / wasn't initialised (README claimed fail-closed) | Fails closed by default (`GUARDRAILS_FAIL_OPEN` to override); "developer mode" no longer blocks legit technical questions |
| Gateway | Printed all Portkey headers (incl. API key) to stdout; hardcoded model/user; junk import | No prints; model/slug/config via env |
| API | Errors returned HTTP 200; shared default `thread_id`; no limits/auth; deprecated `on_event` | Real 500s; per-request thread IDs; length validation; optional API key + rate limit + CORS; lifespan; `/health` |
| Ingestion | Random UUIDs → duplicates on re-ingest; PDF fallback pages appended out of order; errors hidden | Deterministic IDs; page order preserved; per-file failure summary + exit code |
| Evals | A failed API call was scored against the *reference* contexts (inflated recall) | Failed calls now score with empty contexts; URL/API key via env |
| Repo | No Docker / tests / CI / license / `.env.example`; 5 MB generated `processed_data` and `.vscode` committed; loose images; unpinned, partly unused requirements | All added/cleaned; pinned lock via `uv pip compile` |
