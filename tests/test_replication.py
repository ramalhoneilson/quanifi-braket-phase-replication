"""Contract checks for saved evidence and comparisons, using stdlib unittest."""

import unittest

import numpy as np

from replicate import (
    native_translation,
    phase_aligned_error,
    process_fidelity,
    q0_left_order,
)


class ComparisonTests(unittest.TestCase):
    def test_asymmetric_state_and_operator_use_same_bit_order(self):
        # Qiskit X(q0)|00> = |01>; in q0-left notation this is |10>.
        from qiskit import QuantumCircuit
        from qiskit.quantum_info import Operator, Statevector
        circuit = QuantumCircuit(2)
        circuit.x(0)
        order = q0_left_order(2)
        state = np.asarray(Statevector.from_instruction(circuit).data)[order]
        operator = Operator(circuit).data[np.ix_(order, order)]
        np.testing.assert_allclose(state, [0, 0, 1, 0])
        np.testing.assert_allclose(operator[:, 0], state)

    def test_global_phase_is_accepted_but_relative_phase_is_rejected(self):
        reference = np.eye(2, dtype=complex)
        global_phase = np.exp(0.7j) * reference
        relative_phase = np.diag([1, -1]).astype(complex)
        self.assertAlmostEqual(process_fidelity(reference, global_phase), 1)
        self.assertLess(phase_aligned_error(reference, global_phase), 1e-12)
        self.assertAlmostEqual(process_fidelity(reference, relative_phase), 0)
        self.assertGreater(phase_aligned_error(reference, relative_phase), 1)

    def test_workaround_emits_only_native_operations(self):
        source = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\n'
                  'h q[0];\ncx q[0],q[1];\n')
        emitted = native_translation(source)
        self.assertNotIn("include", emitted)
        self.assertNotIn("gphase", emitted)
        self.assertNotIn("gate ", emitted)
        self.assertIn("cnot", emitted)


if __name__ == "__main__":
    unittest.main()
