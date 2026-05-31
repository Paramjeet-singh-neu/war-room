"""Thin async OpenAI layer, Weave-instrumented.

Investigators use gpt-4o-mini (fast/cheap); the Commander uses gpt-4o.
If OPENAI_API_KEY is missing we fall back to deterministic mock findings so the
whole pipeline + graph still run for a demo. Every call is a `@weave.op` so it
shows up in the trace tree under its calling agent.
"""
from __future__ import annotations

import os

import weave
from openai import AsyncOpenAI

INVESTIGATOR_MODEL = "gpt-4o-mini"
COMMANDER_MODEL = "gpt-4o"

_client: AsyncOpenAI | None = None


def have_openai() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client


@weave.op()
async def parse_finding(model: str, system: str, user: str, schema_cls):
    """Structured-output call: returns a parsed pydantic object of `schema_cls`."""
    client = _get_client()
    completion = await client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format=schema_cls,
        temperature=0.2,
    )
    return completion.choices[0].message.parsed


@weave.op()
async def chat_text(model: str, system: str, user: str) -> str:
    client = _get_client()
    completion = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
    )
    return completion.choices[0].message.content or ""
