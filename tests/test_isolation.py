"""Check the independent evaluator and the diagnostic update on strided arrays."""

import unittest

import numpy as np
from braket.default_simulator.gate_operations import PauliX

from isolate import expanded_matrix, safe_single_qubit_update


class IsolationTests(unittest.TestCase):
    def test_independent_embedding_respects_control_and_q0_left_order(self):
        operation = PauliX((1, 3), ctrl_modifiers=(1,))
        actual = expanded_matrix(operation, 4)
        expected = np.zeros((16, 16), dtype=complex)
        for column in range(16):
            row = column ^ 1 if column & 4 else column
            expected[row, column] = 1
        np.testing.assert_allclose(actual, expected)

    def test_diagnostic_update_writes_through_noncontiguous_control_slice(self):
        rng = np.random.default_rng(5)
        state = rng.normal(size=(2, 2, 2, 2)) + 1j*rng.normal(size=(2, 2, 2, 2))
        matrix = np.array([[0, 1], [1, 0]], dtype=complex)
        for control, target in [(1, 3), (2, 0)]:
            output = state.copy()
            selection = [slice(None)] * 4
            selection[control] = slice(1, 2)
            selected = tuple(selection)
            safe_single_qubit_update(state[selected], matrix, target, output[selected])
            expected = state.copy()
            zero, one = selection.copy(), selection.copy()
            zero[target], one[target] = 0, 1
            expected[tuple(zero)] = state[tuple(one)]
            expected[tuple(one)] = state[tuple(zero)]
            np.testing.assert_allclose(output, expected)


if __name__ == "__main__":
    unittest.main()
