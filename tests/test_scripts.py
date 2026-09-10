import os
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts"


def test_scripts_executable() -> None:
    missing = [p.name for p in SCRIPTS.glob("*.py") if not os.access(p, os.X_OK)]
    assert not missing, f"not executable: {missing}"
