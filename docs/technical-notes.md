# Technical notes

[Return to the step-by-step guide](../README.md).

These details are optional. Start with the README to run the experiment.

**Update:** the [root-cause investigation](root-cause.md) supersedes the earlier
phase hypothesis for the reproduced Grover split. The original translation
activates a faulty numerical update for generic controlled gates in Braket's
small-circuit simulator. A QASM3-only example, with no phase instructions,
reproduces the defect. The notes below retain the translation mechanics and
the general phase caveats, which remain relevant to circuit validation.

## What the script does

1. Builds the original Grover oracle and two iterations, then emits QASM2 in the
   `{h, cx, rz, x}` basis, just as Quanifi's original builder did.
2. Parses that QASM2 once as the common reference circuit.
3. **Legacy path:** exports QASM3 and replaces `include "stdgates.inc";` with
   Qiskit's actual gate-library text, reproducing the old Braket adapter.
4. **Native path:** reparses QASM2, transpiles to `{h, cx, rz, x}` at optimization
   level zero, removes the include and top-level `gphase`, and renames `cx` to
   Braket's `cnot`. This reproduces Quanifi's workaround.
5. Runs both QASM3 programs on Braket's local `braket_sv` simulator, comparing
   exact states and probabilities against Qiskit's `Statevector` reference.
6. Reconstructs each path's full operator by simulating every computational
   basis input. The reference comes from Qiskit's `Operator`. It also checks
   that each reconstructed operator is unitary and its first column agrees
   with the separate, unmodified zero-input experiment.

Two small controls, `x_gate` and `cx_gate`, are included. In this environment,
both paths pass their **full-operator** comparisons up to global phase. The
Grover composition exposes a disagreement that those controls do not reveal.

The basis-input sweep initializes wires using the built-in `U(0,0,0)` for zero
and `U(pi,0,pi)` for one, avoiding the imported `x` definition. Every wire is
touched in register order. This is instrumentation for operator extraction;
the primary Grover probability experiment adds no such preparation gates.

## Inspect the evidence

Each run creates a fresh `results/<UTC timestamp>/` directory. You can choose a
new directory with `--output results/my-run`; existing directories are never
overwritten. For each case, it saves:

- `input.qasm2`: the common interchange circuit.
- `intermediate.qasm3`: Qiskit's export before either adapter treatment.
- `legacy.qasm3` and `native.qasm3`: the two actual circuit bodies.
- `reference.state.json`, `legacy.state.json`, `native.state.json`: exact states.
- Corresponding `*.unitary.json` files: full operators, unless skipped.
- `result.json`: probabilities, comparison metrics, and optional counts.
- `simulator.log`: Braket's captured feature-support messages.

The run root contains `summary.json`, the exact `stdgates.inc` used, and
`environment.json` with package versions and SHA-256 hashes of the script and
gate library. The script appends `#pragma braket result state_vector` to circuit
bodies for exact execution, or full-register measurement for counts.

All labels and arrays use **q0 leftmost**: index `i` corresponds to the ordinary
binary representation of `i`, interpreted as `q0 q1 ...`. Qiskit's state vectors
and both axes of its operator matrices are explicitly reordered to this
convention. `0110` is palindromic, so use the asymmetric `cx_gate` operator or a
non-palindromic input when independently testing your own bit-order handling.
Complex arrays are JSON objects containing `shape`, `real`, and `imag` arrays;
reconstruct them as `np.array(data["real"]) + 1j*np.array(data["imag"])`.

`sample-results/` contains a checked run from a clean installation of the
committed lockfile, including the generated QASM and matrices. Regenerate your
own results; sample metadata identifies the particular machine used.

## Exercise for circuit-model validation

1. Import `input.qasm2` and model its ideal operator. Check the analytic Grover
   success probability `sin²(5 asin(1/4)) = 0.908447265625`.
2. Model the two QASM3 circuit bodies. Supply the definitions/includes and native
   gate semantics your tool requires, and record those assumptions. A standards
   model and a model of this Braket interpreter need not give the same answer.
3. Compare isolated `X`, isolated `CX`, and the complete Grover circuit. Which
   equivalences can your technique prove, and which counterexamples can it find?
4. Compare full matrices up to **one common global phase**, then compare output
   distributions. Equal distributions for one input do not establish operator
   equivalence. For ideal unitaries, process fidelity
   `|tr(U_ref† U_test)|² / d²` equals one exactly when they differ only by a
   single global phase.
5. Try to reduce the failing circuit. Vary prefixes, wires, and input states;
   keep bit order explicit. Only attribute the failure to `U`, `gphase`, or
   control handling after a smaller discriminating experiment supports it.

The saved metrics include total variation distance for the zero-input output,
state fidelity, process fidelity, and maximum entrywise error after aligning
one global phase. Deterministic historical checks use tolerance `1e-10`.

## Scope of the conclusion

This replicates an **observable difference between translation/execution
routes** in the pinned versions. The imported definitions already contain `U`,
`gphase`, and control modifiers; Braket did not spontaneously replace a gate
with `gphase`. The historical investigation associated the failure with phase
handling, but the new isolation identifies a downstream numerical kernel defect
that explains the observed split. It does not establish a QASM2 translation
error or a blanket violation of the QASM2 standard.

A phase that is global for an isolated operation can become relative when that
operation is controlled. That is motivation for validating phase-sensitive
composition, not a proven minimal root cause for this particular failure.
Dropping top-level global phase is appropriate for the workaround's directly
sampled circuit. Do not assume that a phase-stripped circuit can later be placed
under a control without restoring that phase.

The Qiskit reference and both translation paths share a Qiskit front end; they
are not three completely independent implementations. The analytic success
probability adds an independent check of the reference output. For broader
claims, use an additional independent model and specification-level analysis.
The experiment does not test noise, AWS managed simulators, or QPUs. Its
conclusion is version-specific; it does not assert the behavior of newer releases.

## Provenance and dependencies

The historical fix is in
[`quanifi-experiments`, commit `cf38c07`](https://github.com/ramalhoneilson/quanifi-experiments/commit/cf38c07d9f024c99aefe00d748a39a5f4bdd7aa7),
dated 5 July 2026. It replaced `_inline_stdgates` in both `BraketSimulator` and
`BraketDevice` with the shared `braket_qasm.py` translator and added
`test_grover_phase_kickback_peak`. This standalone script extracts that builder
and translation logic; it does not require importing the original repository.

The direct package versions match the original commit's `uv.lock`: Qiskit
2.3.0, Qiskit QASM3 importer 0.6.0, Braket SDK 1.119.0, Braket default simulator
1.39.4, OpenQASM3 1.0.1, NumPy 2.4.6, and SciPy 1.17.1. The new `uv.lock` pins
the standalone environment's transitive dependencies; those are not claimed to
be a complete copy of the original Quanifi environment.

Source is licensed under **AGPL-3.0-only**, preserving Quanifi's license. The
generated `stdgates.inc` retains its upstream Apache-2.0 notice; see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and [licenses/Apache-2.0.txt](../licenses/Apache-2.0.txt).

## Running without uv

If you prefer pip, use Python 3.12 and run these commands from the repository folder (macOS or Linux):

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python replicate.py --check
```
