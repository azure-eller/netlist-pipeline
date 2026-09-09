"""Opt-in GPU experiment; does not change the production solver or access the database.

Default: the full CuPy multigrid solver in pipeline.fields_gpu. The optional
--backend cudss comparison uses the original CPU assembly with GPU sparse Cholesky;
install nvidia-cudss-cu12==0.8.0.10 and pass --cudss-lib /path/to/libcudss.so.0.
All timings include preparation, transfers and charge extraction. The CPU cache is
cleared for every cut. Run on an otherwise idle machine for reliable comparisons.
"""

from __future__ import annotations

import argparse
import ctypes as ct
import json
import multiprocessing as mp
import random
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

from pipeline import data, fields


class CudssSolver:
    """Small ctypes adapter for the cuDSS 0.8 ABI; all device buffers are FP64.

    The free-node matrix is SPD: positive face conductances with Dirichlet
    conductors anchoring the connected grid. Cholesky therefore applies.
    """

    def __init__(self, library: Path) -> None:
        import torch

        self.torch = torch
        torch.cuda.init()
        self.lib = ct.CDLL(str(library))
        ptr, integer, size = ct.c_void_p, ct.c_int, ct.c_int64
        signatures = {
            "cudssCreate": [ct.POINTER(ptr)],
            "cudssDestroy": [ptr],
            "cudssSetStream": [ptr, ptr],
            "cudssConfigCreate": [ct.POINTER(ptr)],
            "cudssConfigDestroy": [ptr],
            "cudssDataCreate": [ptr, ct.POINTER(ptr)],
            "cudssDataDestroy": [ptr, ptr],
            "cudssMatrixDestroy": [ptr],
            "cudssMatrixCreateDn": [ct.POINTER(ptr), size, size, size, ptr, integer, integer],
            "cudssMatrixCreateCsr": [ct.POINTER(ptr)] + [size] * 3 + [ptr] * 4 + [integer] * 6,
            "cudssExecute": [ptr, integer] + [ptr] * 5,
            "cudssDataGet": [ptr, ptr, integer, ptr, ct.c_size_t, ct.POINTER(ct.c_size_t)],
        }
        for name, arguments in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes = arguments
            function.restype = integer
        self.handle, self.config = ptr(), ptr()
        self.call("cudssCreate", ct.byref(self.handle))
        self.call("cudssConfigCreate", ct.byref(self.config))
        self.call("cudssSetStream", self.handle, torch.cuda.current_stream().cuda_stream)

    def call(self, name: str, *arguments: Any) -> None:
        status = getattr(self.lib, name)(*arguments)
        if status:
            raise RuntimeError(f"{name} failed: cuDSS status {status}")

    def solve(self, matrix: Any, rhs: Any) -> Any:
        torch = self.torch
        matrix = matrix.tocsr()
        matrix.sort_indices()
        n = matrix.shape[0]
        rhs = np.asarray(rhs).reshape(n, -1)
        offsets = torch.as_tensor(matrix.indptr, dtype=torch.int32, device="cuda")
        columns = torch.as_tensor(matrix.indices, dtype=torch.int32, device="cuda")
        values = torch.as_tensor(matrix.data, dtype=torch.float64, device="cuda")
        # Transposed contiguous buffers store the dense matrices in column-major order.
        b = torch.as_tensor(rhs.T.copy(), dtype=torch.float64, device="cuda")
        x = torch.empty_like(b)
        state, a_desc, b_desc, x_desc = (ct.c_void_p() for _ in range(4))
        try:
            self.call("cudssDataCreate", self.handle, ct.byref(state))
            # CUDA_R_32I=10, CUDA_R_64F=1; SPD=3, full=0, zero-based=0.
            self.call(
                "cudssMatrixCreateCsr",
                ct.byref(a_desc),
                n,
                n,
                matrix.nnz,
                offsets.data_ptr(),
                None,
                columns.data_ptr(),
                values.data_ptr(),
                10,
                10,
                1,
                3,
                0,
                0,
            )
            for descriptor, buffer in ((b_desc, b), (x_desc, x)):
                self.call(
                    "cudssMatrixCreateDn",
                    ct.byref(descriptor),
                    n,
                    rhs.shape[1],
                    n,
                    buffer.data_ptr(),
                    1,
                    0,
                )
            # Analyze once and factor once for all right-hand sides.
            for phase in (3, 4, 1008):
                self.call(
                    "cudssExecute",
                    self.handle,
                    phase,
                    self.config,
                    state,
                    a_desc,
                    x_desc,
                    b_desc,
                )
            torch.cuda.synchronize()
            info, written = ct.c_int(), ct.c_size_t()
            self.call(
                "cudssDataGet",
                self.handle,
                state,
                0,
                ct.byref(info),
                ct.sizeof(info),
                ct.byref(written),
            )
            if info.value:
                raise RuntimeError(f"cuDSS numerical failure: {info.value}")
            return x.cpu().numpy().T.copy()
        finally:
            for descriptor in (a_desc, b_desc, x_desc):
                if descriptor.value:
                    self.call("cudssMatrixDestroy", descriptor)
            if state.value:
                self.call("cudssDataDestroy", self.handle, state)

    def close(self) -> None:
        self.call("cudssConfigDestroy", self.config)
        self.call("cudssDestroy", self.handle)


def cpu_cut(cut: Any) -> tuple[Any, float]:
    fields.solve_cut.cache_clear()
    start = time.perf_counter()
    result = fields.solve_cut(cut)
    return result, time.perf_counter() - start


def gpu_cut(cut: Any, solver: Any) -> tuple[Any, float]:
    if solver is not None:
        with patch.object(fields, "spsolve", solver.solve):
            return cpu_cut(cut)
    from pipeline import fields_gpu

    start = time.perf_counter()
    result = fields_gpu.solve_cut(cut)
    return result, time.perf_counter() - start


def compare(reference: Any, result: Any) -> dict[str, float]:
    errors = {}
    for name in ("cmatrix", "cmatrix_air", "z0", "eps_eff", "c11_pf_per_m"):
        expected, actual = np.asarray(getattr(reference, name)), np.asarray(getattr(result, name))
        np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-8)
        errors[name] = float(np.max(np.abs(actual - expected)) / np.max(np.abs(expected)))
    np.testing.assert_allclose(
        [v for _, v in result.coupling],
        [v for _, v in reference.coupling],
        rtol=1e-6,
        atol=1e-8,
    )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("multigrid", "cudss"), default="multigrid")
    parser.add_argument("--cudss-lib", type=Path)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--cpu-workers", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.samples, args.repeats, args.cpu_workers) < 1:
        parser.error("samples, repeats and cpu-workers must be positive")
    rng = random.Random(args.seed)
    cuts = [data.sample_cut(rng) for _ in range(args.samples)]
    report: dict[str, Any] = {"arguments": vars(args).copy(), "cuts": [], "load": []}
    report["arguments"] = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}

    import os

    report["load"].append(os.getloadavg())
    import torch

    if args.backend == "cudss" and args.cudss_lib is None:
        parser.error("--cudss-lib is required for --backend cudss")
    solver = CudssSolver(args.cudss_lib) if args.backend == "cudss" else None
    report["gpu"] = torch.cuda.get_device_name()
    report["torch"] = torch.__version__
    # First call includes context/library initialization and is reported separately.
    start = time.perf_counter()
    gpu_cut(cuts[0], solver)
    report["cold_gpu_seconds"] = time.perf_counter() - start
    for i, cut in enumerate(cuts):
        times: dict[str, list[float]] = {"cpu": [], "gpu": []}
        errors = {}
        for repeat in range(args.repeats):
            results = {}
            for backend in ("cpu", "gpu") if repeat % 2 == 0 else ("gpu", "cpu"):
                if backend == "gpu":
                    result, elapsed = gpu_cut(cut, solver)
                else:
                    result, elapsed = cpu_cut(cut)
                results[backend] = result
                times[backend].append(elapsed)
            errors = compare(results["cpu"], results["gpu"])
        row = {"cut": asdict(cut), "seconds": times, "relative_errors": errors}
        report["cuts"].append(row)
        print(json.dumps({"sample": i, **row}), flush=True)
    sequence = cuts * args.repeats
    with ProcessPoolExecutor(
        max_workers=args.cpu_workers, mp_context=mp.get_context("spawn")
    ) as pool:
        list(pool.map(cpu_cut, cuts[: args.cpu_workers]))
        start = time.perf_counter()
        list(pool.map(cpu_cut, sequence))
        report["cpu_pool_seconds"] = time.perf_counter() - start
    start = time.perf_counter()
    for cut in sequence:
        gpu_cut(cut, solver)
    report["gpu_sequence_seconds"] = time.perf_counter() - start
    report["cuts_in_throughput_test"] = len(sequence)
    report["gpu_speedup_over_cpu_pool"] = (
        report["cpu_pool_seconds"] / report["gpu_sequence_seconds"]
    )
    report["load"].append(os.getloadavg())
    report["gpu_peak_torch_allocated_bytes"] = torch.cuda.max_memory_allocated()
    # PyTorch's allocator metric excludes cuDSS's internal allocations.
    if solver is not None:
        solver.close()
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "cuts"}, indent=2))


if __name__ == "__main__":
    main()
