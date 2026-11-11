#!/usr/bin/env python3
"""Reproduce the historical Quanifi QASM2 -> QASM3 -> Braket discrepancy.

Copyright (C) 2026 Neilson Ramalho
SPDX-License-Identifier: AGPL-3.0-only

Derived from the Quanifi builders and adapters at cf38c07d9f024c99aefe00d748a39a5f4bdd7aa7.
Uses local simulators only. See README.md for provenance and interpretation.
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import sys

import numpy as np
import qiskit
from qiskit import QuantumCircuit, qasm2, qasm3, transpile
from qiskit.circuit.library import grover_operator
from qiskit.quantum_info import Operator, Statevector
from braket.devices import LocalSimulator
from braket.ir.openqasm import Program


BASIS = ["h", "cx", "rz", "x"]
HISTORICAL_COMMIT = "cf38c07d9f024c99aefe00d748a39a5f4bdd7aa7"


def build_cases() -> dict[str, QuantumCircuit]:
    """Keep the original oracle construction and basis-transpilation settings."""
    x = QuantumCircuit(1)
    x.x(0)
    cx = QuantumCircuit(2)
    cx.cx(0, 1)

    marked = "0110"  # q0 is the LEFTMOST bit throughout the exported artifacts.
    oracle = QuantumCircuit(len(marked))
    for i, bit in enumerate(marked):
        if bit == "0":
            oracle.x(i)
    oracle.h(3)
    oracle.mcx([0, 1, 2], 3)
    oracle.h(3)
    for i, bit in enumerate(marked):
        if bit == "0":
            oracle.x(i)
    iteration = grover_operator(oracle, insert_barriers=False)
    grover = QuantumCircuit(4)
    grover.h(range(4))
    for _ in range(2):
        grover.compose(iteration, inplace=True)
    return {"x_gate": x, "cx_gate": cx, "grover_4q": grover}


def q0_left_order(n: int) -> list[int]:
    """Qiskit indexes q[n-1]...q[0]; Braket indexes q[0]...q[n-1]."""
    return [int(f"{i:0{n}b}"[::-1], 2) for i in range(2**n)]


def native_translation(source: str) -> str:
    """The Quanifi workaround: parser -> native basis -> native gate names."""
    circuit = qasm2.loads(source, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    circuit.remove_final_measurements()
    circuit = transpile(circuit, basis_gates=BASIS, optimization_level=0)
    emitted = qasm3.dumps(circuit).replace('include "stdgates.inc";', "")
    emitted = re.sub(r"(?m)^\s*gphase\(.*?\);\s*$", "", emitted)
    return re.sub(r"\bcx\b", "cnot", emitted)


def run_braket(source: str, n: int, log, shots: int = 0):
    if shots:
        # Same explicit full-register measurement used by Quanifi.
        suffix = f"\nbit[{n}] __meas;\n__meas = measure q;\n"
    else:
        suffix = "\n#pragma braket result state_vector\n"
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        result = LocalSimulator("braket_sv").run(
            Program(source=source + suffix), shots=shots
        ).result()
    if shots:
        return dict(sorted((str(k), int(v)) for k, v in result.measurement_counts.items()))
    return np.asarray(result.values[0], dtype=complex)


def extract_unitary(source: str, n: int, log) -> np.ndarray:
    """Simulate every basis input; initialize with U, avoiding the imported x.

    Touch every wire in register order, including zero bits. The primary
    zero-input experiment above does not add these preparation operations.
    """
    columns = []
    marker = f"qubit[{n}] q;"
    if source.count(marker) != 1:
        raise ValueError("Expected exactly one qubit register named q")
    for index in range(2**n):
        bits = f"{index:0{n}b}"
        prep = "\n".join(
            f"U(pi, 0, pi) q[{i}];" if bit == "1" else f"U(0, 0, 0) q[{i}];"
            for i, bit in enumerate(bits)
        )
        prepared = source.replace(marker, marker + "\n" + prep)
        columns.append(run_braket(prepared, n, log))
    return np.column_stack(columns)


def total_variation(a: np.ndarray, b: np.ndarray) -> float:
    return float(0.5 * np.sum(np.abs(a - b)))


def process_fidelity(reference: np.ndarray, actual: np.ndarray) -> float:
    """Unitary equivalence up to ONE phase for the entire operator."""
    d = reference.shape[0]
    return float(abs(np.trace(reference.conj().T @ actual)) ** 2 / d**2)


def phase_aligned_error(reference: np.ndarray, actual: np.ndarray) -> float:
    overlap = np.vdot(reference, actual)
    phase = overlap / abs(overlap) if abs(overlap) > 1e-15 else 1.0
    return float(np.max(np.abs(actual - phase * reference)))


def complex_json(value: np.ndarray) -> dict:
    return {"shape": list(value.shape), "real": value.real.tolist(), "imag": value.imag.tolist()}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def probabilities_dict(probabilities: np.ndarray, n: int) -> dict[str, float]:
    return {f"{i:0{n}b}": float(p) for i, p in enumerate(probabilities)}


def run_case(name: str, circuit: QuantumCircuit, library: str, directory: Path,
             shots: int, unitaries: bool) -> dict:
    directory.mkdir(parents=True)
    # This is also how the historical QiskitGroverCircuit emitted its input.
    basis_circuit = transpile(circuit, basis_gates=BASIS, optimization_level=0)
    input_qasm = qasm2.dumps(basis_circuit)
    parsed = qasm2.loads(input_qasm, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    intermediate = qasm3.dumps(parsed)
    sources = {
        "legacy": intermediate.replace('include "stdgates.inc";', library),
        "native": native_translation(input_qasm),
    }
    (directory / "input.qasm2").write_text(input_qasm, encoding="utf-8")
    (directory / "intermediate.qasm3").write_text(intermediate, encoding="utf-8")
    n = parsed.num_qubits
    order = q0_left_order(n)
    reference = np.asarray(Statevector.from_instruction(parsed).data)[order]
    result = {
        "num_qubits": n,
        "bit_order": "q0_left",
        "reference_probabilities": probabilities_dict(abs(reference)**2, n),
        "paths": {},
    }
    write_json(directory / "reference.state.json", complex_json(reference))
    reference_operator = Operator(parsed).data[np.ix_(order, order)] if unitaries else None
    if unitaries:
        write_json(directory / "reference.unitary.json", complex_json(reference_operator))
    with (directory / "simulator.log").open("w", encoding="utf-8") as log:
        for label, source in sources.items():
            (directory / f"{label}.qasm3").write_text(source, encoding="utf-8")
            state = run_braket(source, n, log)
            if not np.isclose(np.linalg.norm(state), 1, atol=1e-10):
                raise RuntimeError(f"{name}/{label}: state is not normalized")
            probabilities = abs(state)**2
            metrics = {
                "probabilities": probabilities_dict(probabilities, n),
                "total_variation_vs_reference": total_variation(abs(reference)**2, probabilities),
                "state_fidelity_vs_reference": float(abs(np.vdot(reference, state))**2),
                "state_max_error_after_global_phase_alignment": phase_aligned_error(reference, state),
            }
            write_json(directory / f"{label}.state.json", complex_json(state))
            if unitaries:
                unitary = extract_unitary(source, n, log)
                identity_error = float(np.max(np.abs(unitary.conj().T @ unitary - np.eye(2**n))))
                column_error = float(np.max(np.abs(unitary[:, 0] - state)))
                if identity_error > 1e-10 or column_error > 1e-10:
                    raise RuntimeError(f"{name}/{label}: basis sweep is inconsistent or nonunitary")
                metrics.update({
                    "process_fidelity_vs_reference": process_fidelity(reference_operator, unitary),
                    "unitary_max_error_after_global_phase_alignment": phase_aligned_error(reference_operator, unitary),
                    "unitarity_max_error": identity_error,
                    "zero_input_column_max_error": column_error,
                })
                write_json(directory / f"{label}.unitary.json", complex_json(unitary))
            if shots:
                metrics["counts"] = run_braket(source, n, log, shots)
            result["paths"][label] = metrics
    write_json(directory / "result.json", result)
    return result


def historical_checks(cases: dict) -> dict[str, bool]:
    checks = {}
    for name, result in cases.items():
        for label, metrics in result["paths"].items():
            if label == "native" or name in ("x_gate", "cx_gate"):
                checks[f"{name}/{label}: state agrees up to global phase"] = (
                    metrics["state_max_error_after_global_phase_alignment"] < 1e-10
                )
                if "process_fidelity_vs_reference" in metrics:
                    checks[f"{name}/{label}: operator agrees up to global phase"] = (
                        metrics["unitary_max_error_after_global_phase_alignment"] < 1e-10
                    )
    grover = cases["grover_4q"]
    for label, expected in (("legacy", {"0110": 0.457275390625, "0111": 0.457275390625}),
                            ("native", {"0110": 0.908447265625, "0111": 0.006103515625})):
        probabilities = grover["paths"][label]["probabilities"]
        checks[f"grover_4q/{label}: historical probabilities reproduced"] = all(
            abs(probabilities[key] - value) < 1e-10 for key, value in expected.items()
        )
    checks["grover_4q: reference matches analytic success probability"] = (
        abs(grover["reference_probabilities"]["0110"] - np.sin(5 * np.arcsin(1/4))**2) < 1e-10
    )
    return {name: bool(passed) for name, passed in checks.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("results") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    parser.add_argument("--shots", type=int, default=0, help="Also sample Braket counts (unseeded); exact simulation always runs")
    parser.add_argument("--skip-unitaries", action="store_true", help="Skip exhaustive operator extraction; keep exact zero-input states")
    parser.add_argument("--check", action="store_true", help="Exit nonzero if historical expectations are not reproduced")
    args = parser.parse_args()
    if args.shots < 0:
        parser.error("--shots must be nonnegative")
    # Refuse to overwrite results: saved evidence should remain attributable.
    args.output.mkdir(parents=True, exist_ok=False)
    library_path = Path(qiskit.__file__).parent / "qasm" / "libs" / "stdgates.inc"
    library_bytes = library_path.read_bytes()
    library = library_bytes.decode("utf-8")
    (args.output / "stdgates.inc").write_bytes(library_bytes)
    distributions = sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower())
    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {d.metadata["Name"]: d.version for d in distributions},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "stdgates_sha256": hashlib.sha256(library_bytes).hexdigest(),
        "historical_commit": HISTORICAL_COMMIT,
        "arguments": {**vars(args), "output": str(args.output)},
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(args.output / "environment.json", environment)
    cases = {}
    for name, circuit in build_cases().items():
        print(f"Running {name} ...", flush=True)
        cases[name] = run_case(name, circuit, library, args.output / name, args.shots, not args.skip_unitaries)
    checks = historical_checks(cases)
    summary = {"cases": cases, "checks": checks, "historical_discrepancy_reproduced": all(checks.values())}
    write_json(args.output / "summary.json", summary)
    print("\nExact Grover probabilities (q0 leftmost):")
    print(f"{'path':12} {'P(0110)':>12} {'P(0111)':>12} {'TV vs ref':>12}")
    grover = cases["grover_4q"]
    print(f"{'reference':12} {grover['reference_probabilities']['0110']:12.8f} {grover['reference_probabilities']['0111']:12.8f} {0:12.8f}")
    for label, metrics in grover["paths"].items():
        p = metrics["probabilities"]
        print(f"{label:12} {p['0110']:12.8f} {p['0111']:12.8f} {metrics['total_variation_vs_reference']:12.8f}")
    failed = [name for name, passed in checks.items() if not passed]
    print(f"\nHistorical expectations: {'REPRODUCED' if not failed else 'NOT REPRODUCED'}")
    for name in failed:
        print(f"  FAIL: {name}")
    print(f"Artifacts: {args.output}")
    return int(args.check and bool(failed))


if __name__ == "__main__":
    sys.exit(main())
