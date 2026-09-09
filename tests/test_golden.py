import copy
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from pipeline.config import settings
from pipeline.stages import judge as judge_stage

ROOT = Path(__file__).resolve().parents[1]


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("golden", ROOT / "scripts" / "golden.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rules_run() -> tuple[ModuleType, dict[str, Any], dict[str, Any]]:
    g = load_script()
    return g, g.load_expected(), {c: g.score_rules(c) for c in g.CASES}


def test_rules_judge_passes_golden_set(rules_run: tuple[ModuleType, Any, Any]) -> None:
    g, expected, got = rules_run
    passed, cases = g.compare(expected, got, ["score", "violations"])
    assert passed, cases


def test_missing_c1_violation_fails_naming_c1(rules_run: tuple[ModuleType, Any, Any]) -> None:
    g, expected, got = rules_run
    expected = copy.deepcopy(expected)
    expected["pic_cap_far"]["violations"] = [
        v for v in expected["pic_cap_far"]["violations"] if v["ref"] != "C1"
    ]
    passed, cases = g.compare(expected, got, ["score", "violations"])
    assert not passed
    assert not cases["pic_cap_far"]["ok"] and "C1" in cases["pic_cap_far"]["why"]


def test_stage_refuses_unapproved_remote_version(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"name": "learned-gbr", "version": "v9", "artifact_sha256": "ab" * 32}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(settings, "judge_url", "http://judge.test")
    monkeypatch.setattr(settings, "judge_version", "v9")
    asked: list[tuple[str, str, str | None]] = []

    def approved(conn: Any, name: str, version: str, sha: str | None) -> bool:
        asked.append((name, version, sha))
        return False

    monkeypatch.setattr(judge_stage, "approved", approved)
    with pytest.raises(RuntimeError, match="learned-gbr v9"):
        judge_stage.remote_judge(conn=None)
    assert asked == [("learned-gbr", "v9", "ab" * 32)]  # the bytes, not just the name
