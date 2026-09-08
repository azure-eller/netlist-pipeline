"""Judge HTTP contract (SPEC.md "Judge contract") over the reference judge, or over a learned
artifact when JUDGE_VERSION names one. The pipeline points JUDGE_URL at either."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from pipeline import board, judge, learned, models, storage
from pipeline.config import settings

ARTIFACT: dict[str, Any] = {}  # loaded learned judge; empty means the rules judge


def load() -> None:
    """Load the artifact settings name, once. Nothing to do for the rules judge."""
    ARTIFACT.clear()
    version = settings.judge_version
    if version == "rules":
        return
    key = settings.judge_artifact or f"models/judge/{version}.joblib"
    data = Path(key).read_bytes() if Path(key).is_file() else storage.get(key)
    artifact = joblib.load(io.BytesIO(data))
    if artifact["version"] != version:
        raise RuntimeError(
            f"judge artifact {key} is version {artifact['version']!r}, JUDGE_VERSION is {version!r}"
        )
    ARTIFACT.update(
        artifact,
        artifact_sha256=storage.sha256(data),
        loaded_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load()
    yield


app = FastAPI(title="netlist-judge", version=judge.JUDGE["version"], lifespan=lifespan)


class ScoreRequest(BaseModel):
    run_id: int
    netlist: dict[str, Any]
    constraints: dict[str, Any]
    board_pcb: str


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/info")
def info() -> dict[str, Any]:
    if not ARTIFACT:
        return {
            **judge.JUDGE,
            "capabilities": ["score", "violations"],
            "artifact_sha256": None,
            "loaded_at": None,
        }
    return {
        "name": ARTIFACT["name"],
        "version": ARTIFACT["version"],
        "capabilities": ["score"],
        "artifact_sha256": ARTIFACT["artifact_sha256"],
        "loaded_at": ARTIFACT["loaded_at"],
    }


@app.post("/v1/score")
def score(req: ScoreRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if settings.judge_token and authorization != f"Bearer {settings.judge_token}":
        raise HTTPException(401, "bad or missing bearer token")
    b = board.parse(req.board_pcb)
    nl = models.Netlist.from_json(req.netlist)
    c = models.Constraints.from_json(req.constraints)
    if not ARTIFACT:
        return judge.score(b, nl, c).to_json()
    # ponytail: one request, one predict on CPU; request batching and GPU placement are the
    # next rung if a real model ever makes per-call latency matter.
    predicted, feats = learned.predict(ARTIFACT, b, nl, c)
    return {
        "score": predicted,
        "metrics": feats,
        "violations": [],
        "judge": {"name": ARTIFACT["name"], "version": ARTIFACT["version"]},
    }
