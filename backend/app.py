"""FastAPI server: serves the live graph UI and streams orchestration events.

GET  /                  -> the React Flow single-page UI
GET  /api/scenarios     -> list of demo scenarios
POST /api/investigate   -> Server-Sent Events stream of the live orchestration

The SSE stream is the trace the graph animates: dispatch (all edges fire at
once), each finding as it lands, the correlation, then the final hypothesis.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend.llm import commander_model, have_llm, investigator_model, provider  # noqa: E402
from backend.orchestrator import run_incident  # noqa: E402
from backend.scenarios import DEFAULT_SCENARIO, SCENARIOS  # noqa: E402
from backend.weave_setup import init_weave  # noqa: E402

TRACED = init_weave()  # instrument BEFORE any agent logic runs
FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"

app = FastAPI(title="War Room")


class InvestigateRequest(BaseModel):
    scenario: str = DEFAULT_SCENARIO
    # Optional pasted-in raw data (overrides the stored slices for a live paste demo).
    logs: str | None = None
    metrics: str | None = None
    deploys: str | None = None


@app.get("/")
async def index():
    return FileResponse(FRONTEND)


@app.get("/api/status")
async def status():
    return {
        "traced": TRACED,
        "llm": have_llm(),
        "provider": provider(),
        "investigator_model": investigator_model(),
        "commander_model": commander_model(),
    }


@app.get("/api/scenarios")
async def scenarios():
    return [
        {"id": s["id"], "title": s["title"], "alert": s["alert"],
         "logs": s["logs"], "metrics": s["metrics"], "deploys": s["deploys"]}
        for s in SCENARIOS.values()
    ]


@app.post("/api/investigate")
async def investigate(req: InvestigateRequest):
    base = SCENARIOS.get(req.scenario)
    if base is None:
        raise HTTPException(404, f"unknown scenario '{req.scenario}'")

    scenario = dict(base)  # shallow copy; override raw slices if pasted in
    for field in ("logs", "metrics", "deploys"):
        val = getattr(req, field)
        if val:
            scenario[field] = val

    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    async def emit(event: dict):
        await queue.put(event)

    async def driver():
        try:
            await run_incident(scenario, emit)
        except Exception as e:  # surface errors into the stream instead of hanging
            await queue.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            await queue.put(SENTINEL)

    async def event_stream():
        task = asyncio.create_task(driver())
        try:
            while True:
                event = await queue.get()
                if event is SENTINEL:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
