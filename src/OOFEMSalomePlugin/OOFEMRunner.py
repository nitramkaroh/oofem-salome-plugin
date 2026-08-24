"""OOFEM executable discovery and headless solver execution."""

from dataclasses import dataclass
import os
import re
import shutil
import subprocess


class OOFEMSolverError(RuntimeError):
    pass


_ERROR_SUMMARY_RE = re.compile(r"(?<!\d)(\d+)\s+error\(s\)", re.IGNORECASE)


def solver_error_counts(output):
    """Return every numeric ``N error(s)`` summary emitted by OOFEM."""
    return [int(match.group(1)) for match in _ERROR_SUMMARY_RE.finditer(output or "")]


def solver_output_succeeded(returncode, output, require_summary=False):
    """Evaluate completion without the unsafe ``0``-inside-``10`` substring test."""
    counts = solver_error_counts(output)
    return (
        returncode == 0
        and (bool(counts) or not require_summary)
        and all(count == 0 for count in counts)
    )


@dataclass
class SolverRunResult:
    command: list
    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self):
        combined = "{}\n{}".format(self.stdout, self.stderr).lower()
        return solver_output_succeeded(
            self.returncode, combined, require_summary=True
        )


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


def probe_solver_version(executable=None, timeout=2.0):
    """Return concise OOFEM version provenance, or None when unavailable."""
    executable = resolve_executable(executable)
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "-v"],
            capture_output=True,
            text=True,
            check=False,
            timeout=max(0.1, min(10.0, float(timeout))),
        )
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    lines = []
    for raw_line in "{}\n{}".format(
        completed.stdout or "", completed.stderr or ""
    ).splitlines():
        line = raw_line.strip()
        if (
            line.startswith("OOFEM version ")
            or line.startswith("Git RepoURL:")
            or line.startswith("Branch:")
            or line.startswith("Hash:")
        ):
            lines.append(line)
    return "; ".join(lines) if lines else None


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
