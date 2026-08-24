"""OOFEM executable discovery and headless solver execution."""

from dataclasses import dataclass
import os
import shutil
import subprocess


class OOFEMSolverError(RuntimeError):
    pass


@dataclass
class SolverRunResult:
    command: list
    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self):
        combined = "{}\n{}".format(self.stdout, self.stderr).lower()
        return self.returncode == 0 and "error(s)" in combined and "0 error(s)" in combined


def resolve_executable(configured_path=None):
    candidates = [configured_path, os.environ.get("OOFEM_BIN"), shutil.which("oofem")]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = os.path.abspath(os.path.expanduser(candidate))
        if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
            return resolved
    return None


def resolve_timeout(timeout=None):
    """Resolve an explicit timeout or the typed SALOME preference contract."""
    if timeout is None:
        timeout = os.environ.get("OOFEM_SOLVER_TIMEOUT", "300")
    try:
        return max(1, min(86400, int(timeout)))
    except (TypeError, ValueError):
        return 300


def run_solver(input_file, executable=None, timeout=None):
    input_file = os.path.abspath(input_file)
    if not os.path.isfile(input_file):
        raise OOFEMSolverError("OOFEM input does not exist: {}".format(input_file))

    executable = resolve_executable(executable)
    if executable is None:
        raise OOFEMSolverError(
            "OOFEM executable not found. Select it in the Export tab or set OOFEM_BIN."
        )

    command = [executable, "-f", input_file]
    completed = subprocess.run(
        command,
        cwd=os.path.dirname(input_file) or None,
        capture_output=True,
        text=True,
        check=False,
        timeout=resolve_timeout(timeout),
    )
    result = SolverRunResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if not result.succeeded:
        message = result.stderr.strip() or result.stdout.strip()
        raise OOFEMSolverError(
            "OOFEM failed with exit code {}.\n{}".format(result.returncode, message)
        )
    return result
