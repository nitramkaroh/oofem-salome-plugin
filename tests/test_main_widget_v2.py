"""Integration tests for the version-2 controls in OOFEMMainWidget."""

import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.test_main_widget as legacy_widget_tests


try:
    from OOFEMSalomePlugin.OOFEMQt import QtCore
except Exception:  # pragma: no cover - environment without Qt
    QtCore = None

QtWidgets = legacy_widget_tests.QtWidgets
_QT_IMPORT_ERROR = legacy_widget_tests._QT_IMPORT_ERROR


@unittest.skipUnless(
    QtWidgets is not None,
    "PyQt5 or PySide2 is required for MainWidget v2 tests: {}".format(
        _QT_IMPORT_ERROR
    ),
)
class MainWidgetV2Tests(unittest.TestCase):
    def setUp(self):
        patches = [
            unittest.mock.patch.object(
                QtWidgets.QMessageBox, "information", lambda *args, **kwargs: None
            ),
            unittest.mock.patch.object(
                QtWidgets.QMessageBox, "warning", lambda *args, **kwargs: None
            ),
            unittest.mock.patch.object(
                QtWidgets.QMessageBox, "critical", lambda *args, **kwargs: None
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        self.widget = legacy_widget_tests.OOFEMMainWidget()
        self.addCleanup(self.widget.deleteLater)
        self.mesh = legacy_widget_tests.FakeMesh()
        study_object = legacy_widget_tests.FakeStudyObject(
            "0:1:2", "Panel", self.mesh
        )
        self.study = legacy_widget_tests.FakeStudy([study_object])

    def test_populate_migrates_legacy_assignments_and_scalar_bcs(self):
        legacy_state = {
            "materials": [
                {
                    "id": "mat-1",
                    "name": "sheet",
                    "oofem_type": "ElasticIsotropic2d",
                    "assigned_group": "MAT_FACES",
                    "params": {"E": 1000.0, "nu": 0.25, "t": 0.5},
                }
            ],
            "bcs": [
                {
                    "id": "fixed",
                    "name": "fixed",
                    "oofem_type": "Displacement",
                    "assigned_group": "BC_FIXED",
                    "params": {"dof": 1, "val": 0.0},
                }
            ],
        }

        self.widget.populateAll(study=self.study, state=legacy_state)

        self.assertEqual(self.widget.state["schema_version"], 2)
        self.assertEqual(
            self.widget.state["bcs"][0]["params"],
            {"dofs": [1], "values": [0.0]},
        )
        self.assertEqual(len(self.widget.state["cross_sections"]), 1)
        self.assertEqual(self.widget.crossSectionTable.rowCount(), 1)
        self.assertEqual(self.widget.timeFunctionTable.rowCount(), 1)

    def test_explicit_entities_reach_exporter_validation(self):
        self.widget.populateAll(study=self.study)
        default_function_id = self.widget.state["time_functions"][0]["id"]
        self.widget.state["materials"] = [
            {
                "id": "mat-1",
                "name": "sheet",
                "oofem_type": "ElasticIsotropic2d",
                "params": {"E": 1000.0, "nu": 0.25},
            }
        ]
        self.widget.state["cross_sections"] = [
            {
                "id": "cs-1",
                "name": "sheet section",
                "oofem_type": "simplecs",
                "material_id": "mat-1",
                "assigned_group": "MAT_FACES",
                "params": {"thick": 1.0},
            }
        ]
        self.widget.state["bcs"] = [
            {
                "id": "fixed",
                "name": "fixed",
                "oofem_type": "Displacement",
                "assigned_group": "BC_FIXED",
                "time_function_id": default_function_id,
                "params": {
                    "dofs": [1, 2],
                    "values": [0.0, 0.0],
                },
            },
            {
                "id": "load",
                "name": "traction",
                "oofem_type": "SurfaceLoad",
                "assigned_group": "LOAD_EDGE",
                "time_function_id": default_function_id,
                "params": {"dofs": [1], "components": [1.0]},
            },
        ]
        self.widget.populateMaterials()
        self.widget.populateCrossSections()
        self.widget.populateBCs()

        summary = self.widget.validateModel()

        self.assertEqual(summary["cross_sections"], 1)
        self.assertEqual(summary["time_functions"], 1)
        self.assertEqual(self.widget.crossSectionTable.rowCount(), 1)
        self.assertEqual(self.widget.bcTable.columnCount(), 4)

    def test_analysis_editor_updates_canonical_model_and_integer_params(self):
        self.widget.populateAll(study=self.study)
        linear_index = self.widget.analysisCombo.findData("linearstatic")
        self.assertGreaterEqual(linear_index, 0)

        self.widget.analysisCombo.setCurrentIndex(linear_index)
        self.widget.analysisPropsTable.item(0, 1).setText("3")

        self.assertEqual(
            self.widget.state["analysis"]["oofem_type"], "linearstatic"
        )
        self.assertEqual(self.widget.state["analysis"]["params"]["nsteps"], 3)

    def test_salome_execution_preferences_feed_runtime_settings(self):
        self.widget.populateAll(study=self.study)
        with tempfile.TemporaryDirectory() as directory:
            captured = {}

            def choose_file(parent, title, default_name, file_filter):
                del parent, title, file_filter
                captured["default_name"] = default_name
                return "", ""

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "OOFEM_WORKING_DIRECTORY": directory,
                    "OOFEM_RESULTS_DIRECTORY": directory,
                    "OOFEM_SOLVER_TIMEOUT": "17",
                    "OOFEM_AUTO_OPEN_PARAVIS": "true",
                },
                clear=False,
            ), unittest.mock.patch.object(
                QtWidgets.QFileDialog,
                "getSaveFileName",
                choose_file,
            ):
                self.widget._chooseInputFilename()
                settings = self.widget._selectedSolverSettings()
                self.assertEqual(self.widget._solverTimeoutSeconds(), 17)
                self.assertTrue(self.widget._autoOpenParaVis())

            self.assertEqual(settings["output_directory"], directory)
            self.assertEqual(
                captured["default_name"],
                os.path.join(directory, "Panel.in"),
            )

    def test_cancel_requests_terminate_and_sets_cancelled_state(self):
        self.widget.populateAll(study=self.study)

        class FakeProcess:
            def __init__(self):
                self.terminated = False
                self.killed = False

            def state(self):
                return QtCore.QProcess.Running

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        process = FakeProcess()
        self.widget.solverProcess = process
        with unittest.mock.patch.object(
            QtCore.QTimer, "singleShot", lambda *args, **kwargs: None
        ):
            self.assertTrue(self.widget.cancelSolver())

        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)
        self.assertTrue(self.widget._solver_cancelled)
        self.assertFalse(self.widget.cancelRunBtn.isEnabled())


if __name__ == "__main__":
    unittest.main()
