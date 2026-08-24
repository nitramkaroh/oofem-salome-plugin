import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "tools" / "salome_acceptance.py"


def load_acceptance_module():
    spec = importlib.util.spec_from_file_location("salome_acceptance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acceptance = load_acceptance_module()


class SalomeAcceptanceUnitTests(unittest.TestCase):
    def test_split_modules_accepts_salome_separators_and_deduplicates(self):
        self.assertEqual(
            acceptance.split_modules("OOFEM,SMESH:PARAVIS;OOFEM"),
            ["OOFEM", "SMESH", "PARAVIS"],
        )

    def test_report_fails_only_for_failed_checks(self):
        report = acceptance.AcceptanceReport()
        report.pass_check("pass")
        report.skip_check("skip", "not requested")
        self.assertTrue(report.ok)
        report.fail_check("fail", "broken")
        self.assertFalse(report.ok)

    def test_pvd_fixture_is_relative_and_well_formed(self):
        with tempfile.TemporaryDirectory() as directory:
            pvd = acceptance.write_pvd_fixture(directory)
            root = ET.parse(str(pvd)).getroot()
            dataset = root.find("./Collection/DataSet")
            self.assertEqual(dataset.get("file"), "oofem-step-1.vtu")
            self.assertTrue((Path(directory) / dataset.get("file")).is_file())


@unittest.skipUnless(
    os.environ.get("OOFEM_SALOME_ROOT"),
    "set OOFEM_SALOME_ROOT to run the real SALOME 9.16 acceptance test",
)
class RealSalomeAcceptanceTests(unittest.TestCase):
    def test_terminal_runtime_acceptance(self):
        salome_root = Path(os.environ["OOFEM_SALOME_ROOT"]).resolve()
        launcher = salome_root / "salome-oofem"
        self.assertTrue(launcher.is_file(), launcher)

        with tempfile.TemporaryDirectory(prefix="oofem-salome-xdg-") as config:
            environment = os.environ.copy()
            environment["XDG_CONFIG_HOME"] = config
            environment["OOFEM_ACCEPTANCE_SOURCE_ROOT"] = str(REPOSITORY_ROOT)
            if os.environ.get("OOFEM_BIN"):
                environment["OOFEM_ACCEPTANCE_OOFEM_BIN"] = os.environ["OOFEM_BIN"]
            completed = subprocess.run(
                [
                    str(launcher),
                    "start",
                    "-t",
                    "-w",
                    "1",
                    "--nosave-config",
                    "--modules=OOFEM,SMESH,PARAVIS",
                    str(SCRIPT),
                ],
                cwd=str(REPOSITORY_ROOT),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=180,
            )

        marker_lines = [
            line
            for line in completed.stdout.splitlines()
            if line.startswith(acceptance.RESULT_PREFIX)
        ]
        self.assertTrue(marker_lines, completed.stdout)
        result = json.loads(marker_lines[-1][len(acceptance.RESULT_PREFIX) :])
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertTrue(result["ok"], completed.stdout)
        statuses = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual(statuses["runtime_discovery"], "pass")
        self.assertEqual(statuses["native_callback_payload_roundtrip"], "pass")
        self.assertEqual(
            statuses["salomeds_attribute_hdf_roundtrip"], "pass"
        )
        expected_pipeline = "pass" if os.environ.get("OOFEM_BIN") else "skip"
        self.assertEqual(statuses["smesh_oofem_paraview"], expected_pipeline)
        self.assertEqual(statuses["paravis_pvd"], "pass")
        self.assertEqual(statuses["gui_module_activation"], "skip")


if __name__ == "__main__":
    unittest.main()
