"""Weave Evaluation — measure root-cause accuracy on a held-out incident set.

This is the third layer of Weave usage (after Inference + Tracing): we don't just
*trace* War Room, we *evaluate* it. A `weave.Evaluation` runs the crew over 5
incidents with known root causes and scores the Commander's ranking, so agent
prompts / models can be iterated against a real metric and compared on a Weave
leaderboard.

Pitch: "The crew that debugs production is itself measurable in production."

Run:
    .venv/bin/python -m backend.eval          # uses your provider (wandb/openai/mock)

With WANDB_API_KEY + WANDB_PROJECT set, results + traces land in your Weave project
under the Evals tab. Without a W&B login it runs a local scored summary instead.
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

import weave  # noqa: E402

from backend.agents import investigate  # noqa: E402
from backend.correlation import rank_hypotheses  # noqa: E402
from backend.hard_scenarios import HARD_EVAL_SET  # noqa: E402
from backend.llm import commander_model, investigator_model, provider  # noqa: E402
from backend.scenarios import EVAL_SET  # noqa: E402
from backend.tools import IncidentDataSource  # noqa: E402
from backend.weave_setup import init_weave  # noqa: E402

SOURCES = ["logs", "metrics", "deploys"]

# WARROOM_EVAL selects the dataset: demo (5 curated) | hard (12 ambiguous) | all (17)
_SETS = {"demo": EVAL_SET, "hard": HARD_EVAL_SET, "all": EVAL_SET + HARD_EVAL_SET}


def _eval_set() -> list[dict]:
    return _SETS.get(os.environ.get("WARROOM_EVAL", "hard"), HARD_EVAL_SET)


@weave.op()
async def predict(incident: dict) -> dict:
    """Run the crew (parallel investigators + programmatic correlation) on one incident."""
    ds = IncidentDataSource(incident)
    findings = await asyncio.gather(*(investigate(s, incident, ds) for s in SOURCES))
    ranked = rank_hypotheses(findings, incident["incident_start"])
    return {
        "top_cause": ranked[0].cause,
        "top_confidence": ranked[0].confidence,
        "ranked_causes": [h.cause for h in ranked],
    }


def _match(cause: str, truth: str, aliases: list[str]) -> bool:
    """Cause id matches the ground truth by exact/substring or a known alias.

    Deploy/config ids match verbatim; a semantic cause (e.g. an expired TLS cert)
    matches whether the model labels it 'cert-expiry' or 'tls-certificate-expiration'.
    """
    c = cause.lower()
    t = truth.lower()
    if t in c or c in t:
        return True
    return any(a.lower() in c for a in aliases)


@weave.op()
def rca_scorer(ground_truth_cause: str, ground_truth_aliases: list[str], output: dict) -> dict:
    """Score the Commander's ranking against the known root cause."""
    causes = output["ranked_causes"]
    rank = 0
    for i, c in enumerate(causes):
        if _match(c, ground_truth_cause, ground_truth_aliases):
            rank = i + 1
            break
    return {
        "top1_correct": _match(output["top_cause"], ground_truth_cause, ground_truth_aliases),
        "found_in_ranking": rank > 0,
        "truth_rank": rank,
        "reciprocal_rank": (1.0 / rank) if rank else 0.0,
    }


def _rows() -> list[dict]:
    return [
        {
            "incident": s,
            "ground_truth_cause": s["ground_truth_cause"],
            "ground_truth_aliases": s.get("ground_truth_aliases", []),
        }
        for s in _eval_set()
    ]


async def _run_weave_eval():
    evaluation = weave.Evaluation(
        name="war-room-root-cause-accuracy",
        dataset=_rows(),
        scorers=[rca_scorer],
    )
    print(f"[eval] Running weave.Evaluation over {len(_eval_set())} incidents "
          f"(investigators={investigator_model()}, commander={commander_model()})…")
    summary = await evaluation.evaluate(predict)
    print("[eval] Weave summary:", summary)
    print("[eval] Open the Evals tab in your Weave project to see the leaderboard.")


async def _run_local_eval():
    print(f"[eval] No W&B login — running a LOCAL scored summary "
          f"(provider={provider()}). Set WANDB_API_KEY + WANDB_PROJECT to log to Weave.\n")
    rows = _rows()
    results = []
    for row in rows:
        out = await predict(row["incident"])
        sc = rca_scorer(row["ground_truth_cause"], row["ground_truth_aliases"], out)
        results.append((row["incident"]["id"], row["ground_truth_cause"], out["top_cause"], sc))
        mark = "✓" if sc["top1_correct"] else "✗"
        print(f"  {mark} {row['incident']['id']:16} truth={row['ground_truth_cause']:18} "
              f"-> top={out['top_cause']:18} rank={sc['truth_rank']}")
    n = len(results)
    acc = sum(r[3]["top1_correct"] for r in results) / n
    mrr = sum(r[3]["reciprocal_rank"] for r in results) / n
    print(f"\n[eval] top-1 accuracy = {acc:.0%} ({sum(r[3]['top1_correct'] for r in results)}/{n})   MRR = {mrr:.3f}")


def main():
    traced = init_weave()
    asyncio.run(_run_weave_eval() if traced else _run_local_eval())


if __name__ == "__main__":
    main()
