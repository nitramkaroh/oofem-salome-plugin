"""Tests for headless runner settings shared with SALOME preferences."""

import os
import pathlib
import sys
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from OOFEMSalomePlugin.OOFEMRunner import resolve_timeout


class RunnerPreferenceTests(unittest.TestCase):
    def test_explicit_timeout_has_priority_and_is_bounded(self):
        with unittest.mock.patch.dict(
            os.environ, {"OOFEM_SOLVER_TIMEOUT": "77"}, clear=False
        ):
            self.assertEqual(resolve_timeout(12), 12)
            self.assertEqual(resolve_timeout(0), 1)
            self.assertEqual(resolve_timeout(999999), 86400)

    def test_timeout_uses_environment_contract(self):
        with unittest.mock.patch.dict(
            os.environ, {"OOFEM_SOLVER_TIMEOUT": "45"}, clear=False
        ):
            self.assertEqual(resolve_timeout(), 45)

    def test_invalid_environment_timeout_falls_back_safely(self):
        with unittest.mock.patch.dict(
            os.environ, {"OOFEM_SOLVER_TIMEOUT": "invalid"}, clear=False
        ):
            self.assertEqual(resolve_timeout(), 300)


if __name__ == "__main__":
    unittest.main()
