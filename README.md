# 🛰️ War Room — a multi-agent incident-response crew

> When production breaks at 3am, one on-call engineer alt-tabs between logs,
> metrics, and deploy history, holding three investigation threads in their
> head at once. **War Room replaces that with a crew**: three specialist agents
> investigate *in parallel*, and an **Incident Commander** correlates their
> findings across time to return a ranked root-cause hypothesis and a suggested
> action.

Mess in (raw logs + metrics + deploys) → **root cause out**.

![War Room graph](docs/demo.png)

---

## What it does (2 sentences)

War Room dispatches a production alert to three specialist investigators — **Logs**,
**Metrics**, and **Deploys** — that run concurrently and each return a *structured
finding*. The **Incident Commander** correlates those findings programmatically (by
`points_to` convergence and timestamp alignment) and emits a ranked root-cause
hypothesis with a suggested action and an aggregate confidence — e.g. *"Error spike
at 14:32 correlates with deploy-4471 to payments-service at 14:30 — likely root
cause. Roll back #4471. Confidence: high."*

## Why it's a *crew* and not a script

Remove the multi-agent part and you're back to one overloaded human. The value is
**parallel fan-out + cross-source correlation**: the three investigators look at
different evidence simultaneously, and the Commander is the only component that can
*correlate across sources* — something no single agent (and no exhausted human at
3am) does well.

---

## Architecture

```
                 ┌─────────────────────────────────────────┐
                 │            Incident Commander            │   gpt-4o
                 │  dispatch · correlate · rank · adjudicate │
                 └───────┬───────────┬───────────┬──────────┘
        A2A Task ────────┤           │           ├──────── A2A Task
                         ▼           ▼           ▼
                   ┌─────────┐ ┌─────────┐ ┌─────────┐        gpt-4o-mini
                   │  Logs   │ │ Metrics │ │ Deploys │  ← run via asyncio.gather
                   │  agent  │ │  agent  │ │  agent  │
                   └────┬────┘ └────┬────┘ └────┬────┘
                        │ MCP tool  │ MCP tool  │ MCP tool   (each sees ONLY its slice)
                   ┌────▼────┐ ┌────▼────┐ ┌────▼────┐
                   │  logs   │ │ metrics │ │ deploys │
                   └─────────┘ └─────────┘ └─────────┘
                        │           │           │
                        └─────► structured Finding ◄────┘  (the only thing the
                                  (JSON schema)             Commander ever sees)
```

### A2A-style message passing
The Commander↔investigator handoffs are modeled as **Agent-to-Agent** messages:
the Commander dispatches a structured [`Task`](backend/schema.py) (source + incident
window + instruction) and each investigator replies with a structured
[`Finding`](backend/schema.py). No free-form prose crosses the boundary.

### MCP-framed data sources
Each production data source is exposed as an **MCP**-style tool/resource in
[`backend/tools.py`](backend/tools.py): `fetch_logs`, `fetch_metrics`, `fetch_deploys`,
each taking the incident window and returning a raw slice. An investigator calls
**only its own** source's tool — clean separation of concerns. In a full deployment
these are three MCP servers; here they're in-process tools over the same contract.

### Real async parallelism (non-negotiable)
The three investigators run with `asyncio.gather` in
[`backend/orchestrator.py`](backend/orchestrator.py). Verified: all three start at
t=0.0s and total runtime equals the *slowest* agent, not the sum. Sequential
execution would defeat the entire point.

### Programmatic correlation (not text concatenation)
[`backend/correlation.py`](backend/correlation.py) ranks candidate causes purely from
the structured fields:
- **convergence** — how many distinct sources' `points_to` name the same cause,
- **timestamp alignment** — how tightly each finding's `timestamp` sits around the
  incident start (a *change* that immediately precedes the symptoms scores highest),
- plus the agents' own `severity`/`confidence` and a bonus for "this was an actual change".

The Commander LLM only writes the *narrative* on top of an already-computed ranking —
it never produces the ranking. This is what reads as sound architecture in Q&A.

### The finding schema (locked)
```json
{
  "source": "logs | metrics | deploys",
  "finding": "human-readable description of what was observed",
  "timestamp": "ISO-8601 of the key observed event",
  "severity": "low | medium | high | critical",
  "confidence": 0.0,
  "points_to": "short id of the suspected cause, e.g. 'deploy-4471'"
}
```

---

## Sponsor tools — every tool, and how

### Weights & Biases **Weave** (observability) — *"debugging the debugger"*
We use Weave to **trace and debug a crew whose entire job is debugging production
incidents — observability all the way down.** Weave is wired in at commit #1, before
any agent logic:

- `weave.init("war-room")` in [`backend/weave_setup.py`](backend/weave_setup.py).
- **Every** agent op is decorated with `@weave.op()`: each investigator
  (`investigate`), each MCP tool fetch (`fetch_logs`/`fetch_metrics`/`fetch_deploys`),
  the structured-output LLM calls (`parse_finding`, `chat_text`), the Commander
  synthesis (`commander_synthesize`), and the top-level `run_incident`.
- Result: one Weave trace tree per incident showing the parallel fan-out, every
  agent's inputs/outputs (raw slice → structured finding), and the Commander's
  correlation — exactly the view you want when *the crew itself* misbehaves.

The setup is **offline-tolerant**: if there's no W&B login it runs untraced (decorators
stay active) instead of blocking on a key prompt. Run `wandb login` (or set
`WANDB_API_KEY`) and restart to log traces.

### OpenAI
`gpt-4o-mini` for the three investigators (fast/cheap), `gpt-4o` for the Commander
(synthesis + adjudication). See [`backend/llm.py`](backend/llm.py).

### LangChain
Not used. We deliberately chose plain async OpenAI function-/structured-output calls
to keep the harness legible and avoid an unfamiliar framework under time pressure;
the A2A/MCP contracts are explicit in our own schema rather than hidden in a framework.

---

## Run it

```bash
./run.sh                 # creates .venv, installs deps, serves on http://127.0.0.1:8000
# then open http://127.0.0.1:8000
```

**Runs with zero keys** in deterministic *mock mode* (great for a bulletproof demo).
To go fully live:

```bash
cp .env.example .env     # add OPENAI_API_KEY  (real LLM reasoning + live paste)
wandb login              # (or set WANDB_API_KEY) to log Weave traces
```

The header chips show whether you're on **GPT-4o live / mock LLM** and **Weave
tracing / off** at a glance.

> Note: in mock mode the investigators return curated findings, so pasting *new*
> raw data won't change the verdict. With `OPENAI_API_KEY` set, the agents reason
> over whatever you paste — the live "mess in → root cause out" payoff.

---

## The demo (2 minutes)

1. **Stakes (1 sentence):** "It's 3am, payments are throwing 500s, and you're the
   only one awake."
2. Pick the **payments** scenario → the textareas fill with the messy raw signals.
3. Hit **🚨 Trigger Incident** → all three edges fire **at once** (the parallel
   moment), each investigator node lights up and resolves to a structured finding.
4. The Commander pulses, draws **gold correlation lines** from the converging
   sources, then turns **green**: *"Root cause: deploy-4471 · critical → Roll back
   deploy-4471."*
5. **Bonus:** switch to the **db-vs-deploy** scenario — Logs blames the database,
   but Deploys + Metrics blame the timing of deploy-4480, and the Commander
   **adjudicates** the disagreement in the verdict panel.

---

## Layout

```
backend/
  app.py            FastAPI: serves the UI, streams orchestration via SSE
  orchestrator.py   Incident Commander — asyncio.gather fan-out + synthesis
  agents.py         the three investigators (A2A Task in, Finding out)
  correlation.py    programmatic hypothesis ranking (convergence + alignment)
  schema.py         locked Task / Finding / Hypothesis models
  tools.py          MCP-framed data sources (logs/metrics/deploys)
  llm.py            async OpenAI layer (Weave-instrumented) + mock fallback
  weave_setup.py    offline-tolerant weave.init
  scenarios.py      two engineered incident scenarios
frontend/
  index.html        live React Flow graph (loaded via ESM CDN — no build step)
```

Built entirely at the event.
