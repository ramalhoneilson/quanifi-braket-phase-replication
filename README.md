# Reproduce the Quanifi / Braket circuit problem

This exercise shows a problem we found when translating a quantum circuit
from OpenQASM 2 (QASM2) to OpenQASM 3 (QASM3) and running it with Amazon Braket.
QASM is a text format for describing quantum circuits.

We use a four-qubit Grover search circuit. It searches for `0110` and runs two
Grover iterations. The script compares three ways of running the same circuit:

| Name in the output | What it means |
| --- | --- |
| `reference` | The expected result, calculated with Qiskit. |
| `legacy` | Braket using Quanifi's old translation. This reproduces the problem. |
| `native` | Braket using Quanifi's corrected translation. This matches the reference. |

Everything runs on your computer. You do not need AWS credentials or a
Quanifi/NiFi installation.

**Root cause identified:** the reproduced Grover failure comes from a numerical
update in Braket's simulator, triggered by the old translation's controlled-gate
instructions. A smaller QASM3-only example reproduces it without any phase
instructions. See [the root-cause investigation](docs/root-cause.md).

## Step 1: Check that you have the tools

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and access to this private GitHub repository. Ask the repository owner for
access if you do not have it.

Open a terminal and check:

```sh
git --version
uv --version
```

Both commands should print a version number. If a command is missing, install
that tool first. The project uses Python 3.12; uv can download it when needed.

## Step 2: Download the repository

Run:

```sh
git clone https://github.com/ramalhoneilson/quanifi-braket-phase-replication.git
cd quanifi-braket-phase-replication
```

If you already downloaded the repository, open a terminal in its folder and
continue with Step 3. Run all remaining commands from that folder.

## Step 3: Install the Python packages

Run:

```sh
uv sync --locked
```

Wait for it to finish. This installs the package versions saved in the
repository, so everyone uses the same environment. Internet access is needed
for this installation.

## Step 4: Run the experiment

Run:

```sh
uv run --locked python replicate.py --check
```

The script runs the examples and saves the results. You should see a table
like this:

```text
path              P(0110)      P(0111)    TV vs ref
reference      0.90844727   0.00610352   0.00000000
legacy         0.45727539   0.45727539   0.45117187
native         0.90844727   0.00610352   0.00000000

Historical expectations: REPRODUCED
```

`REPRODUCED` means the script found the original problem and confirmed that
the corrected translation fixes this example.

## Step 5: Understand the result

`P(0110)` means the probability of measuring `0110`.

- The reference gives about **90.84%** for the state we searched for.
- The old translation splits the main result: about **45.73%** for `0110`
  and **45.73%** for `0111`.
- The corrected translation gives about **90.84%** for `0110`, matching the reference.

The last column measures how different the output probabilities are from the
reference. Zero means they match.

These probabilities come from exact simulation. Running the command again
should give the same values, apart from tiny rounding differences.

## Step 6: Open the saved circuits

The last line printed by the script shows the results folder. Open its
`grover_4q` subfolder. Start with these files:

| File | What to look at |
| --- | --- |
| `input.qasm2` | The original circuit in QASM2. |
| `legacy.qasm3` | The QASM3 program produced by the old translation. |
| `native.qasm3` | The QASM3 program produced by the corrected translation. |
| `result.json` | The probabilities and comparison results. |

The `*.state.json` files contain state vectors. The `*.unitary.json` files
contain the circuit matrices. These are useful if your validation technique
works with states or matrices.

You can also inspect [sample-results/grover_4q](sample-results/grover_4q)
without installing anything. It contains a saved, checked run.

## Step 7: Try your own validation technique

1. Load `input.qasm2` into your tool and calculate its expected behavior.
2. Load `legacy.qasm3` and `native.qasm3`. Record which gate definitions your
   tool uses to interpret them.
3. Compare your results with the saved probabilities and matrices. Can your
   technique detect the difference observed with the old translation?
4. Try the smaller examples in the `x_gate` and `cx_gate` result folders.
   Those examples agree with the reference up to global phase. Can your tool
   confirm that agreement too?

All saved bit strings put **qubit 0 on the left**. For example, `10` means
qubit 0 is 1 and qubit 1 is 0. Keep this order when comparing results.

A global phase multiplies the whole state by the same complex factor and
leaves measurement probabilities unchanged. Allow for it when comparing
matrices. Matching probabilities for one input does not prove that two
circuits behave the same for every input.

## Optional: Simulate repeated measurements

To also collect 4,096 measurement outcomes for each Braket circuit, run:

```sh
uv run --locked python replicate.py --check --shots 4096
```

These counts are saved in `result.json`. They vary between runs, but their
proportions should be close to the exact probabilities.

## If something goes wrong

- **GitHub says “repository not found”:** check that you have access and are
  signed in to GitHub through your Git client.
- **The script says the output folder already exists:** run the basic command
  from Step 4. It chooses a new folder automatically.
- **You see `NOT REPRODUCED`:** check that you used `uv sync --locked` and
  `uv run --locked`. Keep the output folder, including `environment.json`,
  so the package versions and failed checks can be inspected.

## What this example establishes

It reproduces different results from the two translation routes with the saved
package versions. Further investigation isolates the observed split to
Braket's handling of a controlled gate on certain qubit positions. The simulator
can write an update into a temporary copy instead of its output buffer.

The old translation activates that execution path. Using Braket's native
`cnot` avoids it. The earlier phase explanation was a hypothesis; it is not
the established cause of this reproduced failure.

To reproduce the isolation and a temporary diagnostic repair, run:

```sh
uv run --locked python isolate.py --check
```

This command checks the translation stages, finds the first failing circuit
prefix, and tests a smaller example. It temporarily replaces one function in
memory, then restores it; no installed package files are changed. See the
[root-cause report](docs/root-cause.md) for the evidence and its limits.

For the translation details, matrix format, original sources, and additional
research questions, see the [technical notes](docs/technical-notes.md).

The code uses the [AGPL-3.0 license](LICENSE). Third-party notices are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
