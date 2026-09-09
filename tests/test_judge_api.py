from collections.abc import Iterator
from pathlib import Path

import joblib
import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import GradientBoostingRegressor

from pipeline import board, judge_api, learned
from pipeline.config import settings
from pipeline.models import Constraints
from tests.test_judge import board as make_board
from tests.test_judge import fp, netlist

B = make_board([fp("U1", 0, 0, ["N1"]), fp("R1", 10, 0, ["N1"])])  # N1 unrouted
BODY = {
    "run_id": 1,
    "netlist": netlist({"N1": ["U1.1", "R1.1"]}).to_json(),
    "constraints": Constraints().to_json(),
    "board_pcb": "(kicad_pcb)",
}


@pytest.fixture(autouse=True)
def parse_to_b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(board, "parse", lambda text: B)


@pytest.fixture
def learned_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    rows = [
        list(learned.features(B, netlist({"N1": ["U1.1", "R1.1"]}), Constraints()).values())
    ] * 2
    model = GradientBoostingRegressor(n_estimators=2).fit(rows, [-10.0, -10.0])
    path = tmp_path / "vtest.joblib"
    joblib.dump(
        {
            "name": "learned-gbr",
            "version": "vtest",
            "model": model,
            "feature_names": learned.FEATURES,
        },
        path,
    )
    monkeypatch.setattr(settings, "judge_version", "vtest")
    monkeypatch.setattr(settings, "judge_artifact", str(path))
    with TestClient(judge_api.app) as client:
        yield client
    judge_api.ARTIFACT.clear()


def test_rules_info_and_score() -> None:
    with TestClient(judge_api.app) as client:
        assert client.get("/v1/info").json() == {
            "name": "rules",
            "version": "0.1.0",
            "capabilities": ["score", "violations"],
            "artifact_sha256": None,
            "loaded_at": None,
        }
        r = client.post("/v1/score", json=BODY).json()
    assert [v["rule"] for v in r["violations"]] == ["unrouted"]
    assert r["judge"] == {"name": "rules", "version": "0.1.0"}


def test_learned_info_and_score(learned_client: TestClient) -> None:
    info = learned_client.get("/v1/info").json()
    assert (info["name"], info["version"], info["capabilities"]) == (
        "learned-gbr",
        "vtest",
        ["score"],
    )
    assert len(info["artifact_sha256"]) == 64 and info["loaded_at"]
    r = learned_client.post("/v1/score", json=BODY).json()
    assert isinstance(r["score"], float) and r["violations"] == []
    assert r["judge"] == {
        "name": "learned-gbr",
        "version": "vtest",
        "artifact_sha256": info["artifact_sha256"],  # every verdict names the bytes
    }
    assert set(r["metrics"]) == set(learned.FEATURES)


def test_version_mismatch_fails_startup(
    learned_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "judge_version", "v9")
    with pytest.raises(RuntimeError, match="version 'vtest', JUDGE_VERSION is 'v9'"):
        judge_api.load()
