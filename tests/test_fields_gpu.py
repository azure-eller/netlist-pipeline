"""Explicit GPU verification; skipped on machines without the optional CUDA stack."""

import random

import numpy as np
import pytest

pytest.importorskip("cupy")
# The project's CUDA PyTorch installation supplies the CUDA shared libraries.
pytest.importorskip("torch")

from pipeline import data, fields, fields_gpu  # noqa: E402
from pipeline.windows import Conductor, Cut  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def gpu_available() -> None:
    try:
        count = fields_gpu.cp.cuda.runtime.getDeviceCount()
    except fields_gpu.cp.cuda.runtime.CUDARuntimeError:
        pytest.skip("CUDA device unavailable")
    if count == 0:
        pytest.skip("CUDA device unavailable")


@pytest.mark.parametrize("sample_index", [0, 1, 3, 4, 5])
def test_gpu_labels_match_cpu_oracle(sample_index: int) -> None:
    rng = random.Random(1000)
    cut = [data.sample_cut(rng) for _ in range(sample_index + 1)][-1]
    reference = fields.solve_cut(cut)
    diagnostics = {}
    result = fields_gpu.solve_cut(cut, diagnostics=diagnostics)
    assert reference is not None and result is not None
    for name in ("cmatrix", "cmatrix_air", "z0", "eps_eff", "c11_pf_per_m"):
        np.testing.assert_allclose(
            getattr(result, name), getattr(reference, name), rtol=1e-6, atol=1e-8
        )
    np.testing.assert_allclose(
        [v for _, v in result.coupling],
        [v for _, v in reference.coupling],
        rtol=1e-6,
        atol=1e-8,
    )
    assert all(solve["residual"] <= 2e-11 for solve in diagnostics["solves"])


def test_gpu_no_reference_after_neighbour_selection() -> None:
    cut = Cut(
        0,
        0,
        "F.Cu",
        (Conductor(0, 1, "T"), Conductor(0.1, 0.2, "N")),
        False,
        False,
        0.2,
        0.035,
        4.5,
    )
    assert fields.solve_cut(cut) is None
    assert fields_gpu.solve_cut(cut) is None
