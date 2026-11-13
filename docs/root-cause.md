# The reproduced Grover failure is in a simulator update

[Back to the main guide](../README.md).

**The observed split is caused by a numerical update in Braket's state-vector
simulator.** The old QASM translation reaches that update through a generic
controlled gate. The native `cnot` workaround uses a different update and avoids
the failure. The earlier explanation involving phase conventions was a
hypothesis; this investigation supersedes it for this reproduced example.

## 1. Run the investigation

After the main guide's installation steps, run:

```sh
uv run --locked python isolate.py --check
```

Look for `Isolation checks: PASS`. Here, PASS means that the defect, its cause,
and the diagnostic repair were all reproduced as expected.

The command saves a new folder under `results/`. A checked run is included in
[sample-isolation-results](../sample-isolation-results). Existing output folders
are never overwritten. The investigation uses the repository's locked versions,
including Braket default simulator **1.39.4** and NumPy **2.4.6**.

## 2. A much smaller example

This program has no QASM2 translation, `gphase`, custom gate definitions, or
Grover algorithm:

```qasm
OPENQASM 3.0;
qubit[4] q;
i q[0];
i q[2];
x q[1];
ctrl @ x q[1], q[3];
```

It prepares `0100` and should flip the last bit, producing `0101`.
The identity operations keep the two idle wires present in Braket's simulation.
All labels put qubit 0 on the left.

| Last instruction | Expected output | Actual output in the pinned simulator |
| --- | --- | --- |
| `cnot q[1], q[3];` | `0101` | `0101` |
| `ctrl @ x q[1], q[3];` | `0101` | `0100` |
| `ctrl @ U(pi, 0, pi) q[1], q[3];` | `0101` | `0100` |

These are deterministic outputs. The script checks exact states and also
records 128 sampled outcomes for each original program. In this example the
generic controlled operation is effectively skipped.

The script also tests every distinct control/target pair on registers of two,
three, and four qubits, preparing the control as one and the target as zero.
Among these 20 layout tests, the failing layouts are four qubits with
`(control, target) = (1, 3)` and `(2, 0)`. This is a bounded layout experiment,
not a claim that these are the only possible failing cases.

## 3. The particular code that loses the update

The relevant implementation is in Braket's
[`linalg_utils.py`, version 1.39.4](https://github.com/amazon-braket/amazon-braket-default-simulator-python/blob/v1.39.4/src/braket/default_simulator/linalg_utils.py).

`multiply_matrix` selects the part of the state where the control has the
required value, then passes that selection to a single-qubit update routine.
For the four-qubit example it selects an array shaped `(2, 1, 2, 2)`.

`_apply_single_qubit_gate_small` reshapes that output selection to `(4, 2, 1)`
before writing the changed amplitudes. For this layout, reshaping creates a
**copy**, rather than a view of the original output buffer. The routine writes
into that copy and returns the unchanged original buffer.

`isolate.py` checks this directly using `numpy.shares_memory`, then invokes the
original routine on a small array to demonstrate the lost write. The output
also records the installed source's hash and whether it matches the package's
RECORD file. No installed dependency files are edited.

## 4. Connect the small example to the full Grover failure

The script checks the full operators at each stage, allowing one global phase:

| Stage | Agrees with the original Grover operator? |
| --- | --- |
| Qiskit transpilation to the builder's gate basis | Yes |
| QASM2 export and re-import | Yes |
| QASM3 export and re-import with Qiskit | Yes |
| Braket's interpreted instructions, executed by an independent dense evaluator | Yes |
| Braket's normal state-vector execution of the old route | No |
| Same program with the one numerical update temporarily replaced | Yes |

The independent evaluator uses the gate matrices and qubit operands produced
by Braket's interpreter. It embeds them in full matrices using explicit bit
indices and multiplies those matrices, without using Braket's state-vector
update functions. It is independent of the suspect numerical update; it is
not an independent implementation of all of Braket's primitive gate semantics.

For the zero-input Grover run, prefixes 1 through 24 agree up to global phase.
The first difference appears at **operation 25: `cx q[1], q[3]`**. This is a
one-based index in the emitted QASM2 instruction sequence. The script saves
the failing prefix and both states. Identity preparations are added to prefix
tests to keep their full four-qubit width; the main complete-circuit comparison
uses the original program without these additions.

## 5. Change only the numerical update

As a diagnostic experiment, the script temporarily replaces
`_apply_single_qubit_gate_small` with a function that writes directly into
indexed slices of the original output. It changes no QASM text, gate matrix,
phase rule, or control interpretation.

| Grover measurement | Original update | Diagnostic update |
| --- | ---: | ---: |
| P(`0110`) | 0.457275390625 | 0.908447265625 |
| P(`0111`) | 0.457275390625 | 0.006103515625 |

The full 16 × 16 operator agrees with the reference after this replacement,
up to global phase and floating-point tolerance (`1e-10`). Its process fidelity
changes from approximately **0.3678226471** to **1**. All 20 tested control/target
layouts also give their expected results with the diagnostic replacement.

The replacement applies only within a Python context and is then restored.
It is evidence about the cause, not a production patch validated against every
simulator feature, optimization, or supported circuit size.

## 6. What this changes about the original interpretation

For this case, the evidence identifies a **simulator execution defect**, reached
by one translation route. We should no longer describe the Grover split as an
established QASM2-to-QASM3 translation error or as an established `gphase` bug.

The small example needs no phase instructions. The full-circuit repair also
requires no change to phase handling. This does not establish that every other
aspect of phase handling in Braket is correct; it isolates the cause of the
specific reproduced failure in this pinned environment.

This distinction matters when applying a model-validation technique: a model
of the intended QASM semantics could accept both equivalent programs. Comparing
that model with actual execution can expose the faulty implementation path.

This investigation does not test newer Braket versions, managed simulators,
or QPUs. It does not file an upstream issue or modify Quanifi's existing
native-gate workaround.
