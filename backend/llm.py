"""LLM layer — W&B Inference Service first, OpenAI second, deterministic mock last.

Provider selection (lazy, so .env is loaded first):
  * WANDB_API_KEY + WANDB_PROJECT  -> W&B Inference Service (OpenAI-compatible).
        Every call (a) runs on a W&B-hosted open model and (b) is Weave-traced.
        Investigators: meta-llama/Llama-3.3-70B-Instruct  (fast parallel fan-out)
        Commander:     deepseek-ai/DeepSeek-R1-0528        (reasoning-heavy synthesis)
  * OPENAI_API_KEY                 -> OpenAI (gpt-4o-mini / gpt-4o).
  * neither                        -> mock mode (curated findings).

The W&B-hosted models are reached over the OpenAI-compatible endpoint, so we use
the same AsyncOpenAI client — just a different base_url / api_key / project. We do
NOT rely on json_schema structured outputs (not all hosted models support it);
instead we prompt for strict JSON and parse robustly (stripping R1 <think> blocks
and code fences), with 429-aware backoff so the parallel fan-out survives rate limits.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re

import weave
from openai import AsyncOpenAI

WANDB_INFERENCE_BASE = "https://api.inference.wandb.ai/v1"

# Defaults per provider; any can be overridden via env.
_WANDB_INVESTIGATOR = "meta-llama/Llama-3.3-70B-Instruct"
_WANDB_COMMANDER = "deepseek-ai/DeepSeek-V3.1"  # reasoning model for synthesis/adjudication

_client: AsyncOpenAI | None = None


def provider() -> str:
    if os.environ.get("WANDB_API_KEY") and os.environ.get("WANDB_PROJECT"):
        return "wandb"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "mock"


def have_llm() -> bool:
    return provider() != "mock"


# Back-compat alias used elsewhere in the codebase.
def have_openai() -> bool:
    return have_llm()


def investigator_model() -> str:
    p = provider()
    if p == "wandb":
        return os.environ.get("WARROOM_INVESTIGATOR_MODEL", _WANDB_INVESTIGATOR)
    if p == "openai":
        return os.environ.get("WARROOM_INVESTIGATOR_MODEL", "gpt-4o-mini")
    return "mock"


def commander_model() -> str:
    p = provider()
    if p == "wandb":
        return os.environ.get("WARROOM_COMMANDER_MODEL", _WANDB_COMMANDER)
    if p == "openai":
        return os.environ.get("WARROOM_COMMANDER_MODEL", "gpt-4o")
    return "mock"


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if provider() == "wandb":
            _client = AsyncOpenAI(
                base_url=WANDB_INFERENCE_BASE,
                api_key=os.environ["WANDB_API_KEY"],
                project=os.environ["WANDB_PROJECT"],  # required for usage tracking
            )
        else:
            _client = AsyncOpenAI()  # uses OPENAI_API_KEY
    return _client


def _is_rate_limit(e: Exception) -> bool:
    s = f"{type(e).__name__} {e}".lower()
    return "429" in s or "rate" in s or "concurren" in s


def _clean(text: str) -> str:
    """Strip reasoning-model <think> blocks and markdown code fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    return text.strip()


def _extract_json(text: str) -> dict:
    cleaned = _clean(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)  # first {...} blob
    if not m:
        raise ValueError(f"no JSON object found in model output: {cleaned[:200]!r}")
    return json.loads(m.group(0))


async def _create(model: str, system: str, user: str, retries: int = 5):
    client = _get_client()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
            )
        except Exception as e:  # noqa: BLE001 — retry transient errors incl. 429
            last = e
            wait = (2.0 if _is_rate_limit(e) else 0.8) * (2**attempt) + random.uniform(0, 0.4)
            if attempt < retries - 1:
                await asyncio.sleep(min(wait, 20))
    raise last  # type: ignore[misc]


@weave.op()
async def parse_finding(model: str, system: str, user: str, schema_cls):
    """Strict-JSON call -> validated pydantic object of `schema_cls`.

    Retries once with a stricter nudge if the model returns unparseable JSON.
    """
    completion = await _create(model, system, user)
    content = completion.choices[0].message.content or ""
    try:
        return schema_cls.model_validate(_extract_json(content))
    except Exception:
        strict = user + "\n\nReturn ONLY a single JSON object, no prose, no markdown."
        completion = await _create(model, system, strict)
        content = completion.choices[0].message.content or ""
        return schema_cls.model_validate(_extract_json(content))


@weave.op()
async def chat_text(model: str, system: str, user: str) -> str:
    completion = await _create(model, system, user)
    return _clean(completion.choices[0].message.content or "")
