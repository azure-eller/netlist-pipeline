"""Judge HTTP contract (SPEC.md "Judge contract") over the reference judge. A learned model
serves this same surface and the pipeline points JUDGE_URL at it."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from pipeline import board, judge, models
from pipeline.config import settings

app = FastAPI(title="netlist-judge", version=judge.JUDGE["version"])


class ScoreRequest(BaseModel):
    run_id: int
    netlist: dict[str, Any]
    constraints: dict[str, Any]
    board_pcb: str


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/score")
def score(req: ScoreRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if settings.judge_token and authorization != f"Bearer {settings.judge_token}":
        raise HTTPException(401, "bad or missing bearer token")
    result = judge.score(
        board.parse(req.board_pcb),
        models.Netlist.from_json(req.netlist),
        models.Constraints.from_json(req.constraints),
    )
    return result.to_json()
