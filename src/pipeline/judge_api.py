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

from pipeline import board, db, judge, learned, models, storage
from pipeline.config import settings

ARTIFACT: dict[str, Any] = {}  # loaded learned judge; empty means the rules judge
PHYSICS: dict[str, Any] = {}  # physics provider for the rule judge when not the closed form


def load() -> None:
    """Load the artifact settings name, once. Nothing to do for the rules judge."""
    ARTIFACT.clear()
    PHYSICS.clear()
    version = settings.judge_version
    if version == "rules":
        return
    if version == "oracle":  # the field solver itself, slow, for capturing expected answers
        from pipeline.physics import Oracle

        PHYSICS["provider"] = Oracle()
        return
    key, registered = settings.judge_artifact, None
    if not key:  # the registry names the bytes; a version is one artifact, forever
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("select artifact_key, sha256 from models where version = %s", (version,))
            rows = cur.fetchall()
        if len(rows) != 1:
            raise RuntimeError(f"models table has {len(rows)} rows for version {version!r}")
        key, registered = rows[0]
    data = Path(key).read_bytes() if Path(key).is_file() else storage.get(key)
    sha = storage.sha256(data)
    if registered and sha != registered:
        raise RuntimeError(f"{key} is {sha[:12]}, the models row says {registered[:12]}")
    artifact = joblib.load(io.BytesIO(data))
    if artifact["version"] != version:
        raise RuntimeError(
            f"judge artifact {key} is version {artifact['version']!r}, JUDGE_VERSION is {version!r}"
        )
    ARTIFACT.update(
        artifact,
        artifact_sha256=sha,
        loaded_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    if artifact.get("kind") == "surrogate":  # learned physics behind the full rule judge
        from pipeline.surrogate import Learned

        PHYSICS["provider"] = Learned(artifact)
    elif artifact.get("kind") == "cutnet":  # the cut model: neighbours, plane or not
        from pipeline.cutnet import Learned as CutLearned

        PHYSICS["provider"] = CutLearned(artifact)


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
    if "provider" in PHYSICS:  # oracle or surrogate physics behind the full rule judge
        p = PHYSICS["provider"]
        return {
            "name": p.name,
            "version": p.version,
            "capabilities": ["score", "violations"],
            "artifact_sha256": ARTIFACT.get("artifact_sha256"),
            "loaded_at": ARTIFACT.get("loaded_at"),
        }
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
    if "provider" in PHYSICS:
        out = judge.score(b, nl, c, physics=PHYSICS["provider"]).to_json()
        out["judge"]["artifact_sha256"] = ARTIFACT.get("artifact_sha256")
        return out
    if not ARTIFACT:
        return judge.score(b, nl, c).to_json()
    # ponytail: one request, one predict on CPU; request batching and GPU placement are the
    # next rung if a real model ever makes per-call latency matter.
    predicted, feats = learned.predict(ARTIFACT, b, nl, c)
    return {
        "score": predicted,
        "metrics": feats,
        "violations": [],
        "judge": {
            "name": ARTIFACT["name"],
            "version": ARTIFACT["version"],
            "artifact_sha256": ARTIFACT["artifact_sha256"],
        },
    }
