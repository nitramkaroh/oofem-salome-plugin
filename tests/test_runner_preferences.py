"""Tests for headless runner settings shared with SALOME preferences."""

import os
import pathlib
import stat
import sys
import tempfile
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from OOFEMSalomePlugin.OOFEMRunner import (
    probe_solver_version,
    resolve_timeout,
    solver_error_counts,
    solver_output_succeeded,
)


class RunnerPreferenceTests(unittest.TestCase):
    def test_solver_error_summary_is_parsed_numerically(self):
        self.assertEqual(solver_error_counts("0 error(s)"), [0])
        self.assertEqual(solver_error_counts("10 error(s)"), [10])
        self.assertTrue(solver_output_succeeded(0, "0 error(s)", True))
        self.assertFalse(solver_output_succeeded(0, "10 error(s)", True))

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

    def test_version_probe_extracts_version_branch_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = pathlib.Path(directory) / "oofem"
            executable.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' 'OOFEM version 3.0 (test)' "
                "'Git RepoURL: https://github.com/oofem/oofem.git' "
                "'Branch: main' 'Hash: abc123'\n",
                encoding="utf-8",
            )
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            self.assertEqual(
                probe_solver_version(str(executable)),
                "OOFEM version 3.0 (test); Git RepoURL: "
                "https://github.com/oofem/oofem.git; Branch: main; Hash: abc123",
            )

    def test_version_probe_failure_is_non_fatal(self):
        with unittest.mock.patch.dict(
            os.environ,
            {"OOFEM_BIN": "", "PATH": ""},
            clear=False,
        ):
            self.assertIsNone(probe_solver_version("/missing/oofem"))


if __name__ == "__main__":
    unittest.main()
