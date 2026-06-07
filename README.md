# 🛰️ War Room — a multi-agent incident-response crew

> When production breaks at 3am, one on-call engineer alt-tabs between logs,
> metrics, and deploy history, holding three investigation threads in their
> head at once. **War Room replaces that with a crew**: three specialist agents
> investigate *in parallel*, and an **Incident Commander** correlates their
> findings across time to return a ranked root-cause hypothesis and a suggested
> action.

Mess in (raw logs + metrics + deploys) → **root cause out**.

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

### MCP-framed data sources (incl. one real, live source)
Each production data source is exposed as an **MCP**-style tool/resource in
[`backend/tools.py`](backend/tools.py): `fetch_logs`, `fetch_metrics`, `fetch_deploys`,
each taking the incident window and returning a raw slice. An investigator calls
**only its own** source's tool — clean separation of concerns.

It isn't just canned data: the **Live Incident** scenario lets you paste *real* logs /
metrics, and `fetch_github_changes` pulls **real recent commits from the live GitHub
API** (`owner/name`) as the Deploys agent's slice — so the crew investigates genuinely
unseen data (and the online judge correctly flags weakly-supported verdicts on it).

### Real async parallelism (non-negotiable)
The three investigators run with `asyncio.gather` in
[`backend/orchestrator.py`](backend/orchestrator.py). Verified: all three start at
t=0.0s and total runtime equals the *slowest* agent, not the sum. Sequential
execution would defeat the entire point.

### Dynamic second round — the Commander adapts, it doesn't just fan out once
After round-1 correlation the Commander checks whether the verdict is **contested**
(the top two hypotheses are different causes that are close in score, or the top isn't
a high-confidence call — `_contested` in the orchestrator). If so, it **spawns a one-off
`adjudicator` specialist** (`investigate_specialist`) that deep-dives the leading cause
against the dissent, returns its own structured `Finding`, and the Commander
**re-correlates** with the new evidence. When round 1 is already clear, this is skipped —
the fixed single fan-out remains the default.

This makes the orchestration *adaptive*: e.g. in `db-vs-deploy`, Logs+Metrics see a
`db-connection-pool` symptom while Deploys sees `deploy-4480`; the Commander detects the
contest, spawns the adjudicator (a **5th node that appears live in the graph**), which
confirms the pool exhaustion is downstream of the deploy's `HikariCP 20→5` change — and
the verdict resolves decisively to `deploy-4480`. The whole dynamic tree is Weave-traced.
Verified reliable: db-vs-deploy adjudicates 6/6, payments stays single-round 4/4.

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

Four layers of Weights & Biases usage in one product:

### 1. W&B **Inference Service** (multi-model)
All four agents run on **W&B-hosted open models** via the OpenAI-compatible endpoint
(`https://api.inference.wandb.ai/v1`) — see [`backend/llm.py`](backend/llm.py). We
route by job:
- **Investigators** (Logs / Metrics / Deploys) → `meta-llama/Llama-3.3-70B-Instruct`
  — fast, capable, great for the parallel structured-output fan-out.
- **Incident Commander** → `deepseek-ai/DeepSeek-V3.1` — a reasoning model for the
  synthesis/adjudication step. *We route the hard reasoning to DeepSeek and the
  parallel investigation to Llama.* Both models report usage via the required
  `team/project`. (Override either with `WARROOM_INVESTIGATOR_MODEL` /
  `WARROOM_COMMANDER_MODEL`.)

### 2. W&B **Weave** (tracing) — *"debugging the debugger"*
We use Weave to **trace and debug a crew whose entire job is debugging production
incidents — observability all the way down.** Wired in at commit #1, before any agent
logic:
- `weave.init(WANDB_PROJECT)` in [`backend/weave_setup.py`](backend/weave_setup.py).
- **Every** agent op is `@weave.op()`: each investigator (`investigate`), each MCP
  tool fetch (`fetch_logs`/`fetch_metrics`/`fetch_deploys`), the LLM calls
  (`parse_finding`, `chat_text`), the Commander synthesis (`commander_synthesize`),
  and the top-level `run_incident`.
- Result: one Weave trace tree per incident showing the parallel fan-out, every
  agent's inputs/outputs (raw slice → structured finding), **which model handled
  what**, and the Commander's correlation — exactly the view you want when *the crew
  itself* misbehaves.
- Offline-tolerant: with no W&B login it runs untraced (decorators stay active)
  instead of blocking on a key prompt.

### 3. W&B **Weave Evaluations** (measured, then optimized)
[`backend/eval.py`](backend/eval.py) runs a `weave.Evaluation` over held-out incidents
with known root causes, scoring **top-1 accuracy**, whether the truth is in the ranking,
its rank, and **MRR**. Two datasets ([`scenarios.py`](backend/scenarios.py) +
[`hard_scenarios.py`](backend/hard_scenarios.py), selected with `WARROOM_EVAL=demo|hard|all`):
- **demo** — 5 curated incidents (deploy / config / memory-leak / cache / expired-cert).
- **hard** — **12 deliberately ambiguous incidents** with *red-herring recent deploys*
  (an email-copy deploy, a UI-badge deploy, a retry-tuning deploy that ship right before
  the incident but aren't the cause) and varied non-deploy root causes (external 3rd-party
  outage, disk-full, DNS, secret rotation, redis eviction, JVM heap config, kafka consumer
  config, clock skew, organic traffic surge, blocking DB migration). Designed to punish
  the "always blame the latest deploy" reflex.

```bash
WARROOM_EVAL=hard .venv/bin/python -m backend.eval   # logs to your Weave Evals tab
```

#### We hill-climbed the metric with Weave (the headline)
The hard set started at **8/12 = 67%**. The Weave traces showed *exactly* how it failed:
on `stripe-outage`, `dns-failure`, and `redis-eviction` the crew blamed the **red-herring
deploy** it happened to see in its log slice, instead of the real cause.

| Iteration | Change (driven by reading the traces) | Top-1 |
|---|---|---|
| baseline | — | **67%** |
| 1 | Deploys agent: assess *causal plausibility*, rule out unrelated changes (copy/UI/logging), emit `no-recent-change` | **75%** |
| 2 | Same plausibility lesson to **all** investigators — name the real mechanism (`upstream-stripe-outage`, `dns-resolution-failure`, …) instead of a deploy marker | **92%** |
| 3 | Correlation: **causal-precedence** rule — a plausible change that *preceded* the incident outranks the symptoms it explains (safe now that red herrings are filtered) | **100%** |

The eval also **caught a real regression before the demo did**: iteration 2 made the
Metrics agent name the *symptom* in `db-vs-deploy`, which flipped the demo's headline
verdict from the deploy to the symptom. The causal-precedence rule (iteration 3) fixed
both the eval *and* the demo — full set now **17/17 (1.00)**, MRR 1.00.

#### Online self-evaluation (production scoring, no ground truth)
Offline evals need labels; production doesn't have them. So **every live incident is
scored online** by an LLM-judge (`judge_incident_quality`, a `@weave.op`) that rates how
well-supported the verdict is (0–1) — traced per-incident in Weave and shown live in the
UI (e.g. on real GitHub data it scored a tentative verdict **30% ⚠**, correctly flagging
weak evidence instead of overclaiming). This is the basis for a W&B **Weave Monitor**
(UI-configured) using the same judge to track verdict quality over live traffic.

### 4. W&B **MCP server** (optimize with the coding agent)
The hosted **W&B Weave MCP server** can be registered with a coding agent so it can read
Weave traces / evaluation summaries directly and hill-climb the metric — which is exactly
the loop above.

> Pitch: *"We don't just trace War Room with Weave — we evaluate root-cause accuracy on a
> held-out adversarial incident set and used the traces to hill-climb it from 67% → 100%,
> catching a demo-breaking regression on the way. The crew that debugs production is itself
> measured and optimized in production."*

### OpenAI (alternative path)
If `OPENAI_API_KEY` is set instead of W&B creds, the same code runs on `gpt-4o-mini`
(investigators) / `gpt-4o` (Commander).

### LangChain
Not used — deliberately. Plain async OpenAI-compatible calls keep the harness legible;
the A2A/MCP contracts live in our own schema rather than hidden in a framework.

---

## Run it

```bash
./run.sh                 # creates .venv, installs deps, serves on http://127.0.0.1:8000
# then open http://127.0.0.1:8000
```

**Runs with zero keys** in deterministic *mock mode* (great for a bulletproof demo).
To go fully live on W&B (recommended):

```bash
cp .env.example .env
# set in .env:
#   WANDB_API_KEY=<from https://wandb.ai/authorize>
#   WANDB_PROJECT=<team>/<project>     # e.g. paramjeet/war-room  (REQUIRED, team/project)
./run.sh
```

That single pair of env vars turns on **both** the W&B Inference Service (agents run
on W&B-hosted models) **and** Weave tracing. The header chips show the live provider,
the two models, and tracing status at a glance.

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
  orchestrator.py   Incident Commander — parallel fan-out + dynamic adjudication round
  agents.py         the three investigators + the spawned adjudicator specialist
  correlation.py    programmatic hypothesis ranking (convergence + alignment)
  schema.py         locked Task / Finding / Hypothesis models
  tools.py          MCP-framed data sources + fetch_github_changes (live GitHub)
  llm.py            W&B Inference Service / OpenAI layer (Weave-instrumented) + mock
  weave_setup.py    offline-tolerant weave.init (uses WANDB_PROJECT)
  eval.py           weave.Evaluation — root-cause accuracy (WARROOM_EVAL=demo|hard|all)
  scenarios.py      5 curated incidents (2 demo + 3 eval), with ground truth
  hard_scenarios.py 12 adversarial incidents (red-herring deploys) for the hill-climb
frontend/
  index.html        live React Flow graph (loaded via ESM CDN — no build step)
```

Built entirely at the event.
