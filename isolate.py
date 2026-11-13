#!/usr/bin/env python3
"""Locate the reproduced failure in Braket's controlled-gate numerical kernel.

Copyright (C) 2026 Neilson Ramalho
SPDX-License-Identifier: AGPL-3.0-only

The diagnostic replacement is temporary and restored after each experiment.
No installed package files are modified. See docs/root-cause.md.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import inspect
from pathlib import Path
import platform
from unittest.mock import patch

import numpy as np
from qiskit import QuantumCircuit, qasm2, qasm3, transpile
from qiskit.quantum_info import Operator, Statevector
import braket.default_simulator.linalg_utils as kernels
from braket.default_simulator.openqasm.interpreter import Interpreter

from replicate import (
    BASIS, build_cases, complex_json, extract_unitary, phase_aligned_error,
    probabilities_dict, process_fidelity, q0_left_order, run_braket, write_json,
)


def safe_single_qubit_update(state, matrix, target, out):
    """Diagnostic alternative: index the original output instead of reshaping it.

    This covers the small-circuit kernel in the pinned experiment, not every
    kernel or feature in Braket. Source and destination buffers are distinct.
    """
    zero = [slice(None)] * state.ndim
    one = zero.copy()
    zero[target], one[target] = 0, 1
    z, o = tuple(zero), tuple(one)
    out[z] = matrix[0, 0] * state[z] + matrix[0, 1] * state[o]
    out[o] = matrix[1, 0] * state[z] + matrix[1, 1] * state[o]
    return out, True


def expanded_matrix(operation, n):
    """Independently embed an IR gate in a dense q0-left matrix using bit indices.

    This does not use Braket's state-vector update routines. Gate matrices and
    operands come from its interpreter, so this tests numerical execution of
    that IR, not independently the correctness of every primitive definition.
    """
    control_state = tuple(operation.control_state)
    controls = operation.targets[:len(control_state)]
    targets = operation.targets[len(control_state):]
    matrix = operation.matrix
    full = np.zeros((2**n, 2**n), dtype=complex)
    for column in range(2**n):
        bits = [int(bit) for bit in f"{column:0{n}b}"]
        if any(bits[q] != bit for q, bit in zip(controls, control_state)):
            full[column, column] = 1
            continue
        local_column = sum(bits[q] << (len(targets) - 1 - j)
                           for j, q in enumerate(targets))
        for local_row in range(2**len(targets)):
            output_bits = bits.copy()
            for j, q in enumerate(targets):
                output_bits[q] = (local_row >> (len(targets) - 1 - j)) & 1
            row = sum(bit << (n - 1 - j) for j, bit in enumerate(output_bits))
            full[row, column] = matrix[local_row, local_column]
    return full


def independent_ir_operator(source, n):
    circuit = Interpreter(warn_advanced_features=False).build_circuit(source)
    operator = np.eye(2**n, dtype=complex)
    for operation in circuit.instructions:
        operator = expanded_matrix(operation, n) @ operator
    return operator, circuit


def compare_operators(reference, actual):
    return {
        "process_fidelity": process_fidelity(reference, actual),
        "max_error_up_to_global_phase": phase_aligned_error(reference, actual),
    }


def controlled_x_source(n=4, control=1, target=3, form="controlled_x"):
    """Built-in gates only: no stdgates, gphase, custom gates, or QASM2 input."""
    instruction = {
        "native_cnot": f"cnot q[{control}], q[{target}];",
        "controlled_x": f"ctrl @ x q[{control}], q[{target}];",
        "controlled_U": f"ctrl @ U(pi, 0, pi) q[{control}], q[{target}];",
    }[form]
    # Touch idle wires too; Braket otherwise drops them from the state width.
    identities = "\n".join(f"i q[{q}];" for q in range(n) if q not in (control, target))
    return f"OPENQASM 3.0;\nqubit[{n}] q;\n{identities}\nx q[{control}];\n{instruction}\n"


def memory_probe():
    state = np.zeros((2, 2, 2, 2), dtype=complex)
    state[0, 1, 0, 0] = 1  # |0100>
    output = state.copy()
    selected = output[:, 1:2, :, :]
    reshaped = selected.reshape(4, 2, 1)
    x = np.array([[0, 1], [1, 0]], dtype=complex)
    kernels._apply_single_qubit_gate_small(state[:, 1:2, :, :], x, 3, selected)
    original_result = output.copy()
    safe_single_qubit_update(state[:, 1:2, :, :], x, 3, selected)
    return {
        "controlled_slice_shape": list(selected.shape),
        "controlled_slice_strides": list(selected.strides),
        "reshape_shares_memory_with_output": bool(np.shares_memory(output, reshaped)),
        "original_kernel_probabilities": probabilities_dict(abs(original_result.ravel())**2, 4),
        "diagnostic_kernel_probabilities": probabilities_dict(abs(output.ravel())**2, 4),
    }


def prefix_probe(parsed, library, output, log):
    prefix = QuantumCircuit(parsed.num_qubits)
    rows = []
    order = q0_left_order(parsed.num_qubits)
    for number, instruction in enumerate(parsed.data, 1):
        qubits = [parsed.find_bit(q).index for q in instruction.qubits]
        prefix.append(instruction.operation, qubits)
        source = qasm3.dumps(prefix).replace('include "stdgates.inc";', library)
        marker = f"qubit[{parsed.num_qubits}] q;"
        prep = "\n".join(f"U(0, 0, 0) q[{i}];" for i in range(parsed.num_qubits))
        source = source.replace(marker, marker + "\n" + prep)
        reference = Statevector.from_instruction(prefix).data[order]
        actual = run_braket(source, parsed.num_qubits, log)
        error = phase_aligned_error(reference, actual)
        row = {
            "operation_number_1_based": number,
            "gate": instruction.operation.name,
            "qubits": qubits,
            "parameters": [float(value) for value in instruction.operation.params],
            "state_max_error_up_to_global_phase": error,
        }
        rows.append(row)
        if error > 1e-10:
            (output / "first-failing-prefix.qasm3").write_text(source)
            write_json(output / "prefix-reference.state.json", complex_json(reference))
            write_json(output / "prefix-actual.state.json", complex_json(actual))
            return {"first_failure": row, "tested_prefixes": rows}
    return {"first_failure": None, "tested_prefixes": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("results") /
                        ("isolation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source_file = Path(inspect.getsourcefile(kernels))
    distribution = metadata.distribution("amazon-braket-default-simulator")
    entry = next(item for item in distribution.files
                 if str(item) == "braket/default_simulator/linalg_utils.py")
    encoded_hash = base64.urlsafe_b64encode(hashlib.sha256(source_file.read_bytes()).digest()).rstrip(b"=").decode()
    record_matches = encoded_hash == entry.hash.value
    source_lines = source_file.read_text().splitlines()
    functions = {}
    for name in ["multiply_matrix", "_apply_single_qubit_gate_small"]:
        function_lines, start = inspect.getsourcelines(getattr(kernels, name))
        functions[name] = {"line_start": start, "line_end": start + len(function_lines) - 1}
    write_json(args.output / "environment.json", {
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": {name: metadata.version(name) for name in [
            "amazon-braket-sdk", "amazon-braket-default-simulator", "qiskit", "numpy"]},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "linalg_utils_sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
        "installed_source_matches_package_record": record_matches,
        "source_functions": functions,
        "upstream_source": "https://github.com/amazon-braket/amazon-braket-default-simulator-python/blob/v1.39.4/src/braket/default_simulator/linalg_utils.py",
        "source_line_count": len(source_lines),
    })
    original_kernel = kernels._apply_single_qubit_gate_small
    summary = {"memory": memory_probe(), "minimal": {}, "stages": {}, "layouts": []}
    with (args.output / "simulator.log").open("w") as log:
        for form in ["native_cnot", "controlled_x", "controlled_U"]:
            source = controlled_x_source(form=form)
            (args.output / f"{form}.qasm3").write_text(source)
            state = run_braket(source, 4, log)
            counts = run_braket(source, 4, log, shots=128)
            with patch.object(kernels, "_apply_single_qubit_gate_small", safe_single_qubit_update):
                fixed = run_braket(source, 4, log)
            summary["minimal"][form] = {
                "original_probabilities": probabilities_dict(abs(state)**2, 4),
                "original_counts_128": counts,
                "diagnostic_probabilities": probabilities_dict(abs(fixed)**2, 4),
            }
        print("Minimal example: native cnot -> 0101; ctrl @ x -> 0100 (before diagnostic replacement)", flush=True)

        for n in range(2, 5):
            for control in range(n):
                for target in range(n):
                    if target == control:
                        continue
                    source = controlled_x_source(n, control, target)
                    expected = (1 << (n-1-control)) | (1 << (n-1-target))
                    state = run_braket(source, n, log)
                    with patch.object(kernels, "_apply_single_qubit_gate_small", safe_single_qubit_update):
                        fixed = run_braket(source, n, log)
                    summary["layouts"].append({
                        "num_qubits": n, "control": control, "target": target,
                        "expected_bitstring": f"{expected:0{n}b}",
                        "original_expected_probability": float(abs(state[expected])**2),
                        "diagnostic_expected_probability": float(abs(fixed[expected])**2),
                    })

        grover = build_cases()["grover_4q"]
        reference_operator = Operator(grover).data
        basis = transpile(grover, basis_gates=BASIS, optimization_level=0)
        input_qasm = qasm2.dumps(basis)
        parsed = qasm2.loads(input_qasm, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
        intermediate = qasm3.dumps(parsed)
        roundtrip = qasm3.loads(intermediate)
        library_path = Path(__import__("qiskit").__file__).parent / "qasm/libs/stdgates.inc"
        library = library_path.read_text()
        legacy = intermediate.replace('include "stdgates.inc";', library)
        for name, circuit in [("basis_transpile", basis), ("qasm2_roundtrip", parsed),
                              ("qasm3_roundtrip", roundtrip)]:
            summary["stages"][name] = compare_operators(reference_operator, Operator(circuit).data)
        order = q0_left_order(4)
        reference_operator = reference_operator[np.ix_(order, order)]
        (args.output / "grover-input.qasm2").write_text(input_qasm)
        (args.output / "grover-intermediate.qasm3").write_text(intermediate)
        (args.output / "grover-legacy.qasm3").write_text(legacy)
        independent, ir = independent_ir_operator(legacy, 4)
        summary["stages"]["braket_interpreter_ir_independent_execution"] = compare_operators(reference_operator, independent)
        write_json(args.output / "braket-ir.json", [{
            "gate": op.gate_type, "targets": list(op.targets),
            "control_state": list(op.control_state), "matrix": complex_json(op.matrix),
        } for op in ir.instructions])
        summary["prefix"] = prefix_probe(parsed, library, args.output, log)
        print("First failing prefix:", summary["prefix"]["first_failure"], flush=True)
        state = run_braket(legacy, 4, log)
        original_operator = extract_unitary(legacy, 4, log)
        with patch.object(kernels, "_apply_single_qubit_gate_small", safe_single_qubit_update):
            fixed_state = run_braket(legacy, 4, log)
            fixed_operator = extract_unitary(legacy, 4, log)
        summary["stages"]["braket_original_kernel"] = compare_operators(reference_operator, original_operator)
        summary["stages"]["braket_diagnostic_kernel"] = compare_operators(reference_operator, fixed_operator)
        summary["grover_probabilities"] = {
            "original": probabilities_dict(abs(state)**2, 4),
            "diagnostic": probabilities_dict(abs(fixed_state)**2, 4),
        }
        for label, operator in [("independent-ir", independent), ("original", original_operator),
                                 ("diagnostic", fixed_operator)]:
            write_json(args.output / f"grover-{label}.unitary.json", complex_json(operator))

    checks = {
        "installed_source_matches_package_record": record_matches,
        "reshape_creates_detached_copy": not summary["memory"]["reshape_shares_memory_with_output"],
        "native_cnot_flips_target": summary["minimal"]["native_cnot"]["original_probabilities"]["0101"] > 1-1e-10,
        "controlled_x_misses_target": summary["minimal"]["controlled_x"]["original_probabilities"]["0100"] > 1-1e-10,
        "controlled_U_misses_target": summary["minimal"]["controlled_U"]["original_probabilities"]["0100"] > 1-1e-10,
        "diagnostic_repairs_all_minimal_forms": all(
            value["diagnostic_probabilities"]["0101"] > 1-1e-10 for value in summary["minimal"].values()),
        "diagnostic_repairs_all_tested_layouts": all(
            value["diagnostic_expected_probability"] > 1-1e-10 for value in summary["layouts"]),
        "pre_simulation_stages_preserve_operator": all(
            value["max_error_up_to_global_phase"] < 1e-10 for name, value in summary["stages"].items()
            if name not in ["braket_original_kernel", "braket_diagnostic_kernel"]),
        "original_full_operator_differs": summary["stages"]["braket_original_kernel"]["process_fidelity"] < 0.99,
        "diagnostic_repairs_full_operator": summary["stages"]["braket_diagnostic_kernel"]["max_error_up_to_global_phase"] < 1e-10,
        "diagnostic_restores_grover_probability": abs(float(abs(fixed_state[6])**2)-0.908447265625) < 1e-10,
        "installed_function_restored": kernels._apply_single_qubit_gate_small is original_kernel,
    }
    summary["checks"] = {name: bool(value) for name, value in checks.items()}
    write_json(args.output / "summary.json", summary)
    print("Grover P(0110):", summary["grover_probabilities"]["original"]["0110"], "->",
          summary["grover_probabilities"]["diagnostic"]["0110"])
    failed = [name for name, passed in checks.items() if not passed]
    print("Isolation checks:", "PASS" if not failed else "FAIL")
    for name in failed:
        print(" FAIL:", name)
    print("Artifacts:", args.output)
    return int(args.check and bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
