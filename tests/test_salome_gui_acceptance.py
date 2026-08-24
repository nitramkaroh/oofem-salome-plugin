import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
import uuid


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GUI_SCRIPT = REPOSITORY_ROOT / "tools" / "salome_gui_lifecycle.py"
GUI_RESULT_PREFIX = "OOFEM_SALOME_GUI_RESULT="


def _truthy(value):
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _tagged_processes(run_id):
    """Return processes inheriting this GUI test's unguessable run id."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return {}
    marker = "OOFEM_GUI_RUN_ID={}".format(run_id).encode("utf-8")
    processes = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            environment = (entry / "environ").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if marker not in environment:
            continue
        try:
            raw_command = (entry / "cmdline").read_bytes()
            command = raw_command.replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            ).strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            command = ""
        processes[int(entry.name)] = command
    return processes


def _wait_for_tagged_exit(run_id, timeout=8.0):
    deadline = time.monotonic() + timeout
    while True:
        processes = _tagged_processes(run_id)
        if not processes or time.monotonic() >= deadline:
            return processes
        time.sleep(0.1)


def _terminate_tagged_processes(processes):
    """Clean only processes created by the failed, UUID-tagged test run."""
    for process_id in processes:
        try:
            os.kill(process_id, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(0.5)
    for process_id in processes:
        if not Path("/proc/{}".format(process_id)).exists():
            continue
        try:
            os.kill(process_id, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


@unittest.skipUnless(
    os.environ.get("OOFEM_SALOME_ROOT"),
    "set OOFEM_SALOME_ROOT to run the real SALOME GUI acceptance test",
)
@unittest.skipUnless(
    _truthy(os.environ.get("OOFEM_GUI_ACCEPTANCE")),
    "set OOFEM_GUI_ACCEPTANCE=1 to run the two-session Xvfb GUI test",
)
@unittest.skipUnless(
    shutil.which("xvfb-run"),
    "xvfb-run is required for the real SALOME GUI acceptance test",
)
class RealSalomeGuiAcceptanceTests(unittest.TestCase):
    def _run_phase(
        self,
        launcher,
        directory,
        run_id,
        phase,
        hdf_path,
        result_path,
    ):
        xdg_home = directory / "xdg-{}".format(phase)
        (xdg_home / "salome").mkdir(parents=True)
        port_log = directory / "port-{}.txt".format(phase)
        version_file = (
            launcher.parent
            / "INSTALL"
            / "GUI"
            / "bin"
            / "salome"
            / "VERSION"
        )
        version_line = next(
            line
            for line in version_file.read_text(encoding="utf-8").splitlines()
            if line.startswith("[SALOME GUI]")
        )
        salome_version = version_line.rsplit(":", 1)[1].strip()
        resource_file = (
            xdg_home / "salome" / "SalomeApprc.{}".format(salome_version)
        )
        environment = os.environ.copy()
        environment.update(
            {
                "XDG_CONFIG_HOME": str(xdg_home),
                "QT_QPA_PLATFORM": "xcb",
                "OOFEM_GUI_PHASE": phase,
                "OOFEM_GUI_HDF": str(hdf_path),
                "OOFEM_GUI_RESULT": str(result_path),
                "OOFEM_GUI_RUN_ID": run_id,
            }
        )
        environment.pop("OOFEM_ACCEPTANCE_SOURCE_ROOT", None)

        command = [
            shutil.which("xvfb-run"),
            "-a",
            str(launcher),
            "start",
            "-g",
            "-d",
            "1",
            "-z",
            "0",
            "-a",
            "en",
            "--foreground=1",
            "--nosave-config",
            "--ns-port-log",
            str(port_log),
            "-r",
            str(resource_file),
            "--modules=OOFEM,SMESH,PARAVIS",
        ]
        if phase == "reopen":
            command.append(str(hdf_path))
        command.append(str(GUI_SCRIPT))

        completed = None
        timeout_error = None
        try:
            completed = subprocess.run(
                command,
                cwd=str(REPOSITORY_ROOT),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=90,
            )
        except subprocess.TimeoutExpired as error:
            timeout_error = error
        finally:
            leftovers = _wait_for_tagged_exit(run_id)
            if leftovers:
                _terminate_tagged_processes(leftovers)

        if timeout_error is not None:
            self.fail(
                "{} GUI phase timed out:\n{}".format(
                    phase, timeout_error.stdout or ""
                )
            )
        self.assertFalse(
            leftovers,
            "{} GUI phase left tagged processes: {}".format(phase, leftovers),
        )
        self.assertIsNotNone(completed)
        self.assertTrue(result_path.is_file(), completed.stdout)

        marker_lines = [
            line
            for line in completed.stdout.splitlines()
            if line.startswith(GUI_RESULT_PREFIX)
        ]
        self.assertTrue(marker_lines, completed.stdout)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        streamed = json.loads(
            marker_lines[-1][len(GUI_RESULT_PREFIX) :]
        )
        self.assertEqual(streamed, result)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(result["phase"], phase, completed.stdout)
        self.assertEqual(result["run_id"], run_id, completed.stdout)
        self.assertTrue(result["ok"], completed.stdout)
        return result

    def test_native_gui_hdf_save_and_independent_reopen(self):
        salome_root = Path(os.environ["OOFEM_SALOME_ROOT"]).resolve()
        launcher = salome_root / "salome-oofem"
        self.assertTrue(launcher.is_file(), launcher)
        self.assertTrue(GUI_SCRIPT.is_file(), GUI_SCRIPT)

        with tempfile.TemporaryDirectory(
            prefix="oofem-salome-gui-"
        ) as raw_directory:
            directory = Path(raw_directory)
            run_id = uuid.uuid4().hex
            hdf_path = directory / "oofem-gui-roundtrip.hdf"
            save_result_path = directory / "save-result.json"
            reopen_result_path = directory / "reopen-result.json"

            saved = self._run_phase(
                launcher,
                directory,
                run_id,
                "save",
                hdf_path,
                save_result_path,
            )
            reopened = self._run_phase(
                launcher,
                directory,
                run_id,
                "reopen",
                hdf_path,
                reopen_result_path,
            )

            self.assertGreater(saved["hdf_bytes"], 0)
            self.assertEqual(saved["save_callbacks"], 2)
            self.assertTrue(saved["dirty_bridge"])
            self.assertTrue(saved["manual_commit_absent"])
            self.assertEqual(saved["paraview_button"], "Open in ParaView")
            self.assertEqual(saved["nlgeo"], "off")
            self.assertEqual(saved["contacts"], 1)
            self.assertGreaterEqual(reopened["open_callbacks"], 1)
            self.assertTrue(reopened["widget_restored"])
            self.assertEqual(reopened["project_id"], saved["project_id"])
            self.assertEqual(
                reopened["oofem_executable"],
                saved["oofem_executable"],
            )
            self.assertEqual(reopened["nlgeo"], "off")
            self.assertEqual(reopened["contacts"], 1)


if __name__ == "__main__":
    unittest.main()
