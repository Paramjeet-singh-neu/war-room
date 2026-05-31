# War Room — AGI House submission (copy-paste into app.agihouse.org)

> Fill the `[YOU FILL]` bits, paste the rest. Everything below is ready to go.

---

## Team name
**War Room**   *(or pick your own — must be unique on the platform)*

## Team members
- [YOU FILL] Name — singh.para@northeastern.edu — X: [YOU FILL] / LinkedIn: [YOU FILL]
- *(add teammates if any: name, email, socials)*

## Social handles (for tagging)
- X: [YOU FILL]
- LinkedIn: [YOU FILL]

## Repo
https://github.com/Paramjeet-singh-neu/war-room  (public)

## Demo video
[YOU FILL — under 2 min screen recording. Suggested beats below.]

---

## Project description

### 2–3 sentence summary
War Room is an on-call incident-response crew: when a production system breaks, it
dispatches three specialist agents — Logs, Metrics, and Deploys — that investigate
**in parallel**, each returning a structured finding. An Incident Commander then
**correlates those findings programmatically** (by cause-convergence and timestamp
alignment) and returns a ranked root-cause hypothesis with a suggested action and a
confidence — replacing the one engineer who alt-tabs between three dashboards at 3am.

### What it does & the problem it solves
At 3am a single on-call engineer holds three investigation threads in their head —
logs, metrics, and deploy history — and tries to correlate them under pressure. War
Room makes that a crew: three investigators look at different evidence simultaneously,
and the Commander does the one thing a tired human does worst — *correlate across
sources and time*. You paste in the mess (raw error logs + a metrics summary + a
deploy log); you get back a ranked hypothesis like *"Error spike at 14:32 correlates
with deploy-4471 to payments-service at 14:30 — likely root cause. Roll back #4471.
Confidence: high."* It even adjudicates disagreements (e.g. Logs blames the database,
but Deploys + Metrics blame the deploy, and the Commander resolves that the DB symptom
is downstream of the change).

### How it's built
- **Orchestration / protocols:**
  - **A2A-style message passing** — the Commander dispatches a structured `Task` to each
    investigator and they return a structured `Finding`; no free-form prose crosses the
    boundary (locked schema in `backend/schema.py`).
  - **MCP-framed data sources** — each production data source (logs / metrics / deploys)
    is exposed as an MCP-style tool (`backend/tools.py`); each investigator calls **only
    its own** source, and the Commander never sees raw data — only structured findings
    (clean separation of concerns).
- **Real async parallelism** — the three investigators run under `asyncio.gather`
  (`backend/orchestrator.py`); verified all three start at t=0 and total runtime equals
  the slowest agent, not the sum. Sequential execution would defeat the whole point.
- **Dynamic, adaptive orchestration** — not a fixed single fan-out: when the round-1
  verdict is *contested*, the Commander **spawns a one-off `adjudicator` specialist** that
  deep-dives the conflict and returns its own finding, then **re-correlates** (a 5th node
  appears live in the graph). When round 1 is clear, it's skipped. Verified reliable:
  db-vs-deploy adjudicates 6/6, payments stays single-round 4/4.
- **Programmatic correlation, not text concatenation** — `backend/correlation.py` ranks
  candidate causes from the structured fields: cause-convergence, timestamp alignment,
  and a **causal-precedence** rule (a plausible change that *preceded* the incident
  outranks the symptoms it explains). The Commander LLM only writes narrative on top of
  an already-computed ranking.
- **Agent framework:** plain async OpenAI-compatible function calls (no LangChain — a
  deliberate choice to keep the harness legible and the A2A/MCP contracts explicit).
- **Models (per-agent mix):** Llama-3.3-70B-Instruct for the parallel investigators,
  DeepSeek-V3.1 for the Commander's reasoning-heavy synthesis/adjudication.
- **Stack:** Python + FastAPI; a live **React Flow** orchestration graph (loaded via ESM
  CDN, no build step) animated from a Server-Sent-Events stream of the orchestration —
  edges fire in parallel, nodes light up with findings, the Commander turns green with
  the verdict.

### Every sponsor tool used, and how

**Weights & Biases — used in four layers:**

1. **W&B Inference Service** — all four agents run on W&B-hosted open models via the
   OpenAI-compatible endpoint (`https://api.inference.wandb.ai/v1`). Multi-model by job:
   Llama-3.3-70B-Instruct (investigators) + DeepSeek-V3.1 (Commander). Usage tracked via
   the required `team/project`.
2. **W&B Weave (tracing)** — `weave.init()` at startup and `@weave.op()` on *every* op
   (each investigator, each MCP tool fetch, every LLM call, the Commander synthesis, and
   the top-level run). One trace tree per incident shows the parallel fan-out, each
   agent's raw-slice→finding, which model handled what, and the correlation. Narrative:
   *"observability all the way down — we trace and debug a crew whose entire job is
   debugging."*
3. **W&B Weave Evaluations** — a `weave.Evaluation` over a held-out, **adversarial** set
   of 12 incidents (red-herring recent deploys + varied non-deploy root causes) scoring
   top-1 accuracy / found-in-ranking / rank / MRR. **We used the Weave traces to
   hill-climb the metric 67% → 100%** (sharpen investigator plausibility prompts, then a
   causal-precedence correlation rule) — and the eval *caught a demo-breaking regression*
   on the way. Every run logged to the Evals leaderboard.
4. **W&B Weave MCP server** — registered the hosted MCP server so a coding agent can
   query the traces / evaluation summaries directly and drive the optimization loop.

*(If you used any other sponsor's tool, list it here. We did not use other sponsors.)*

---

## Suggested 2-min demo script
1. **(0:00–0:15) Stakes:** "It's 3am, payments are throwing 500s, you're the only one
   awake, and the answer is buried across logs, metrics, and deploy history."
2. **(0:15–0:55) The loop:** open the app → pick the **payments** scenario (textareas
   fill with the messy raw signals) → hit **🚨 Trigger Incident**. Call out: all three
   edges fire **at once** (real parallelism), each agent returns a structured finding,
   the Commander turns **green**: *"Root cause: deploy-4471 → roll back. Confidence:
   critical."*
3. **(0:55–1:25) Dynamic adjudication (the orchestration money-shot):** switch to
   **db-vs-deploy** → trigger. Logs+Metrics see a DB-pool symptom, Deploys sees the deploy
   → the Commander flags the conflict and **spawns a 5th Adjudicator node live**, which
   deep-dives and resolves it to `deploy-4480` (the pool change). Say: *"the crew doesn't
   just fan out once — when it's unsure it adapts and spins up a specialist."*
4. **(1:20–1:50) Weave (the differentiator):** cut to the **Weave trace tree** (parallel
   fan-out, per-model spans) → cut to the **Evals leaderboard** showing **67% → 100%**.
   Say: *"We used Weave to debug our debugger and hill-climb it on an adversarial set."*
5. **(1:50–2:00) Close:** "Mess in, root cause out — and it's measured in production."
