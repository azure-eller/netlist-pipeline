import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_all_scripts_py_are_executable() -> None:
    missing = [p.name for p in sorted((ROOT / "scripts").glob("*.py")) if not os.access(p, os.X_OK)]
    assert not missing, f"scripts not executable: {missing}"
