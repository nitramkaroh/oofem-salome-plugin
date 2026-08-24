"""Integration tests for the versioned controls in OOFEMMainWidget."""

import os
import pathlib
import sys
import tempfile
import time
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.test_main_widget as legacy_widget_tests
from OOFEMSalomePlugin.OOFEMProject import (
    PROJECT_SCHEMA_VERSION,
    new_project_state,
)


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

        self.assertEqual(
            self.widget.state["schema_version"], PROJECT_SCHEMA_VERSION
        )
        self.assertEqual(
            self.widget.state["bcs"][0]["params"],
            {"dofs": [1], "values": [0.0]},
        )
        self.assertEqual(len(self.widget.state["cross_sections"]), 1)
        self.assertEqual(
            self.widget.state["cross_sections"][0]["element_options"],
            {"nlgeo": "inherit"},
        )
        self.assertEqual(self.widget.crossSectionTable.rowCount(), 1)
        self.assertEqual(self.widget.timeFunctionTable.rowCount(), 1)

    def test_populate_preserves_solver_paths_when_preset_signal_fires(self):
        state = new_project_state()
        state["solver_preset"] = "contact-static-vtk"
        state["oofem_executable"] = "/opt/oofem/bin/oofem"
        state["last_input_file"] = "/tmp/restored-model.in"
        state["contacts"] = [
            {
                "id": "contact-1",
                "name": "restored contact",
                "oofem_type": "StructuralPenaltyContactBC",
                "master_group": "MASTER",
                "slave_group": "SLAVE",
                "time_function_id": "ltf-1",
                "params": {
                    "normal_penalty": 10000.0,
                    "tangential_penalty": 10000.0,
                    "friction": 0.0,
                    "algorithm": 0,
                    "two_pass": False,
                    "reverse_master": False,
                    "reverse_slave": False,
                },
            }
        ]
        events = []
        self.widget.projectChanged.connect(events.append)

        self.widget.populateAll(study=self.study, state=state)

        self.assertEqual(
            self.widget.solverPresetCombo.currentData(), "contact-static-vtk"
        )
        self.assertEqual(self.widget.oofemExecutableEdit.text(), "/opt/oofem/bin/oofem")
        self.assertEqual(self.widget.inputFileEdit.text(), "/tmp/restored-model.in")
        self.assertEqual(state["oofem_executable"], "/opt/oofem/bin/oofem")
        self.assertEqual(state["last_input_file"], "/tmp/restored-model.in")
        self.assertEqual(events, [])

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
                "element_options": {"nlgeo": "off"},
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
        self.assertEqual(self.widget.crossSectionTable.columnCount(), 5)
        self.assertEqual(self.widget.crossSectionTable.item(0, 4).text(), "Off")
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

    def test_initial_condition_tab_reaches_exporter(self):
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
                "oofem_type": "SimpleCS",
                "material_id": "mat-1",
                "assigned_group": "MAT_FACES",
                "params": {"thick": 1.0},
            }
        ]
        self.widget.state["bcs"] = [
            {
                "id": "load",
                "name": "traction",
                "oofem_type": "SurfaceLoad",
                "assigned_group": "LOAD_EDGE",
                "time_function_id": default_function_id,
                "params": {"dofs": [1], "components": [1.0]},
            }
        ]
        self.widget.state["initial_conditions"] = [
            {
                "id": "ic-1",
                "name": "initial velocity",
                "oofem_type": "InitialCondition",
                "assigned_group": "BC_FIXED",
                "params": {
                    "dofs": [1, 2],
                    "conditions": {"v": 0.0},
                },
            }
        ]
        self.widget.populateMaterials()
        self.widget.populateCrossSections()
        self.widget.populateBCs()
        self.widget.populateInitialConditions()

        summary = self.widget.validateModel()

        self.assertEqual(summary["initial_conditions"], 1)
        self.assertEqual(self.widget.initialConditionTable.rowCount(), 1)
        self.assertIn(
            "v=0.0", self.widget.initialConditionTable.item(0, 3).text()
        )

    def test_contact_tab_crud_selects_contact_solver_preset(self):
        self.mesh.groups.append(
            legacy_widget_tests.FakeGroup(
                "CONTACT_EDGE", legacy_widget_tests.SMESH.EDGE, [501]
            )
        )
        self.widget.populateAll(study=self.study)
        created = {
            "name": "interface",
            "oofem_type": "StructuralPenaltyContactBC",
            "master_group": "LOAD_EDGE",
            "slave_group": "CONTACT_EDGE",
            "time_function_id": self.widget.state["time_functions"][0]["id"],
            "params": {
                "normal_penalty": 1000.0,
                "tangential_penalty": 1000.0,
                "friction": 0.0,
                "algorithm": 0,
                "two_pass": False,
                "reverse_master": False,
                "reverse_slave": False,
            },
        }
        edited = dict(created)
        edited["name"] = "edited interface"
        self.widget.analysisCombo.setCurrentIndex(
            self.widget.analysisCombo.findData("linearstatic")
        )

        with unittest.mock.patch(
            "OOFEMSalomePlugin.OOFEMMainWidget.OOFEMContactDialog.run",
            side_effect=[created, edited],
        ):
            self.widget.addContact()
            self.assertEqual(len(self.widget.state["contacts"]), 1)
            contact_id = self.widget.state["contacts"][0]["id"]
            self.assertTrue(contact_id.startswith("contact-"))
            self.assertEqual(self.widget.contactTable.rowCount(), 1)
            self.assertEqual(
                self.widget.solverPresetCombo.currentData(),
                "contact-static-vtk",
            )
            self.assertEqual(
                self.widget.analysisCombo.currentData(), "staticstructural"
            )
            self.assertEqual(
                self.widget.state["analysis"]["params"]["nsteps"], 10
            )
            settings = self.widget._selectedSolverSettings()
            self.assertTrue(settings["vtk"])
            self.assertFalse(settings["nlgeom"])
            for field_id in ("150", "151", "152"):
                self.assertIn(field_id, settings["vtk_record"])
            self.assertFalse(self.widget.solverPresetCombo.isEnabled())
            self.assertFalse(self.widget.analysisCombo.isEnabled())

            self.widget.contactTable.selectRow(0)
            self.widget.editContact()
            self.assertEqual(
                self.widget.state["contacts"][0]["name"],
                "edited interface",
            )
            self.assertEqual(self.widget.state["contacts"][0]["id"], contact_id)

        self.widget.contactTable.selectRow(0)
        self.widget.removeContact()
        self.assertEqual(self.widget.state["contacts"], [])
        self.assertEqual(self.widget.contactTable.rowCount(), 0)
        self.assertTrue(self.widget.solverPresetCombo.isEnabled())
        self.assertTrue(self.widget.analysisCombo.isEnabled())

    def test_adding_contact_preserves_inherited_large_strain_nlgeo(self):
        self.mesh.groups.append(
            legacy_widget_tests.FakeGroup(
                "CONTACT_EDGE", legacy_widget_tests.SMESH.EDGE, [501]
            )
        )
        self.widget.populateAll(study=self.study)
        self.widget.state["cross_sections"] = [
            {
                "id": "cs-large-strain",
                "name": "rubber region",
                "oofem_type": "SimpleCS",
                "material_id": "mat-rubber",
                "assigned_group": "MAT_FACES",
                "element_options": {"nlgeo": "inherit"},
                "params": {"thick": 1.0},
            }
        ]
        self.widget.populateCrossSections()
        self.widget.solverPresetCombo.setCurrentIndex(
            self.widget.solverPresetCombo.findData("large-strain-static-vtk")
        )
        created = {
            "name": "interface",
            "oofem_type": "StructuralPenaltyContactBC",
            "master_group": "LOAD_EDGE",
            "slave_group": "CONTACT_EDGE",
            "time_function_id": self.widget.state["time_functions"][0]["id"],
            "params": {
                "normal_penalty": 1000.0,
                "tangential_penalty": 1000.0,
                "friction": 0.0,
                "algorithm": 0,
                "two_pass": False,
                "reverse_master": False,
                "reverse_slave": False,
            },
        }

        with unittest.mock.patch(
            "OOFEMSalomePlugin.OOFEMMainWidget.OOFEMContactDialog.run",
            return_value=created,
        ):
            self.widget.addContact()

        self.assertEqual(
            self.widget.state["cross_sections"][0]["element_options"],
            {"nlgeo": "on"},
        )
        self.assertEqual(self.widget.crossSectionTable.item(0, 4).text(), "On")
        self.assertEqual(
            self.widget.solverPresetCombo.currentData(), "contact-static-vtk"
        )
        self.assertFalse(self.widget._selectedSolverSettings()["nlgeom"])

    def test_contact_time_function_cannot_be_removed_while_referenced(self):
        self.widget.populateAll(study=self.study)
        function_id = self.widget.state["time_functions"][0]["id"]
        self.widget.state["time_functions"].append(
            {
                "id": "ltf-2",
                "name": "spare",
                "oofem_type": "ConstantFunction",
                "params": {"f(t)": 1.0},
            }
        )
        self.widget.state["contacts"] = [
            {
                "id": "contact-1",
                "name": "interface",
                "time_function_id": function_id,
            }
        ]
        self.widget.populateTimeFunctions()
        warnings = []

        with unittest.mock.patch.object(
            QtWidgets.QMessageBox,
            "warning",
            side_effect=lambda *args: warnings.append(args),
        ):
            self.widget.timeFunctionTable.selectRow(0)
            self.widget.removeTimeFunction()

        self.assertEqual(len(self.widget.state["time_functions"]), 2)
        self.assertEqual(warnings[0][1], "Time Function in Use")
        self.assertIn("contact 'interface'", warnings[0][2])

    def test_loaded_contact_project_is_normalized_without_solver_choice(self):
        self.widget.populateAll(study=self.study)
        state = self.widget.state
        state["analysis"] = {
            "id": "analysis-1",
            "oofem_type": "linearstatic",
            "params": {"nsteps": 1, "nlgeom": False},
        }
        state["solver_preset"] = "linear-static-text"
        state["contacts"] = [
            {
                "id": "contact-loaded",
                "name": "loaded interface",
                "oofem_type": "StructuralPenaltyContactBC",
                "master_group": "LOAD_EDGE",
                "slave_group": "CONTACT_EDGE",
                "time_function_id": state["time_functions"][0]["id"],
                "params": {
                    "normal_penalty": 1000.0,
                    "tangential_penalty": 1000.0,
                    "friction": 0.0,
                    "algorithm": 0,
                    "two_pass": False,
                    "reverse_master": False,
                    "reverse_slave": False,
                },
            }
        ]

        self.widget.populateAll(study=self.study, state=state)

        self.assertEqual(
            self.widget.analysisCombo.currentData(), "staticstructural"
        )
        self.assertEqual(
            self.widget.solverPresetCombo.currentData(), "contact-static-vtk"
        )
        self.assertEqual(self.widget.state["analysis"]["params"]["nsteps"], 10)
        settings = self.widget._selectedSolverSettings()
        self.assertFalse(settings["nlgeom"])
        self.assertTrue(settings["vtk"])
        self.assertFalse(self.widget.analysisCombo.isEnabled())
        self.assertFalse(self.widget.solverPresetCombo.isEnabled())

    def test_solver_check_displays_repository_provenance(self):
        self.widget.populateAll(study=self.study)
        with tempfile.TemporaryDirectory() as directory:
            executable = pathlib.Path(directory) / "oofem"
            executable.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' 'OOFEM version 3.0 (test)' "
                "'Git RepoURL: https://github.com/oofem/oofem.git' "
                "'Branch: main' 'Hash: abc123'\n",
                encoding="utf-8",
            )
            executable.chmod(executable.stat().st_mode | 0o100)
            self.widget.oofemExecutableEdit.setText(str(executable))

            result = self.widget.checkSolver(show_message=False)

        self.assertEqual(result["executable"], str(executable))
        self.assertIn("Git RepoURL:", result["provenance"])
        self.assertIn(
            "https://github.com/oofem/oofem.git",
            self.widget.solverProvenanceLabel.text(),
        )

    def test_solver_success_does_not_hide_manifest_finalization_failure(self):
        self.widget.populateAll(study=self.study)
        self.widget._solver_output_buffer = "0 error(s)\n"
        self.widget._solver_cancelled = False
        self.widget._solver_timed_out = False

        with unittest.mock.patch.object(
            self.widget, "_recordActiveRun", return_value=False
        ), unittest.mock.patch.object(
            self.widget, "refreshRunHistory"
        ), unittest.mock.patch.object(
            self.widget, "refreshResults"
        ), unittest.mock.patch.object(
            self.widget, "_autoOpenParaVis", return_value=False
        ):
            self.widget._solverFinished(0, 0)

        self.assertIn(
            "could not be finalized", self.widget.statusLabel.text()
        )
        self.assertIn(
            "finalization failed", self.widget.exportSummaryLabel.text()
        )

    def test_med_conversion_default_stays_outside_recorded_run(self):
        self.widget.populateAll(study=self.study)
        with tempfile.TemporaryDirectory() as directory:
            run_directory = pathlib.Path(
                directory,
                "oofem-runs",
                "project-1",
                "run-20260824T120000000000Z-aaaaaaaaaaaa",
            )
            source = run_directory / "results" / "model.vtu"
            source.parent.mkdir(parents=True)
            source.write_text("<VTKFile/>", encoding="utf-8")

            with unittest.mock.patch.object(
                self.widget,
                "_selectedRunDirectory",
                return_value=str(run_directory),
            ), unittest.mock.patch.dict(
                os.environ,
                {
                    "OOFEM_WORKING_DIRECTORY": "",
                    "OOFEM_RESULTS_DIRECTORY": "",
                },
                clear=False,
            ):
                default = self.widget._medConversionDefault(str(source))

        self.assertEqual(default, os.path.join(directory, "model.med"))
        self.assertFalse(self.widget._pathWithin(str(run_directory), default))

    def test_run_history_exposes_manifest_time_steps_and_fields(self):
        with tempfile.TemporaryDirectory() as directory, unittest.mock.patch.dict(
            os.environ,
            {"OOFEM_RESULTS_DIRECTORY": directory},
            clear=False,
        ):
            self.widget.populateAll(study=self.study)
            manager = self.widget._getRunManager(prompt=True)
            handle = manager.reserve_run(
                self.widget.state,
                "panel.in",
                ["/bin/true", "-f", "{input}"],
            )
            pathlib.Path(handle.input_file).write_text(
                "{}\ntest\n".format(
                    pathlib.Path(handle.results_directory) / "panel.out"
                ),
                encoding="utf-8",
            )
            manager.register_input(handle.run_id)
            manager.mark_running(handle.run_id, process_id=123)
            results = pathlib.Path(handle.results_directory)
            vtu = results / "panel.out.m0.1.vtu"
            vtu.write_text(
                "<VTKFile><UnstructuredGrid><Piece>"
                "<PointData><DataArray Name=\"Displacement\"/></PointData>"
                "<CellData><DataArray Name=\"Stress\"/></CellData>"
                "</Piece></UnstructuredGrid></VTKFile>",
                encoding="utf-8",
            )
            pvd = results / "panel.out.m0.pvd"
            pvd.write_text(
                "<VTKFile><Collection>"
                "<DataSet timestep=\"1.0\" file=\"panel.out.m0.1.vtu\"/>"
                "</Collection></VTKFile>",
                encoding="utf-8",
            )
            manager.finish_run(
                handle.run_id,
                0,
                result_files=[str(pvd), str(vtu)],
            )
            self.widget.state["last_run_id"] = handle.run_id

            manifests = self.widget.refreshRunHistory()

            self.assertEqual(len(manifests), 1)
            self.assertEqual(
                self.widget.runHistoryCombo.currentData(), handle.run_id
            )
            self.assertIn("time steps: 1", self.widget.runSummaryLabel.text())
            self.assertIn("Displacement", self.widget.runSummaryLabel.text())
            self.assertEqual(self.widget.resultList.count(), 4)
            self.assertEqual(
                self.widget._selectedResultPath(visualization_only=True),
                str(pvd),
            )

    def test_mark_interrupted_recovers_stale_run_without_stopping_qprocess(self):
        with tempfile.TemporaryDirectory() as directory, unittest.mock.patch.dict(
            os.environ,
            {"OOFEM_RESULTS_DIRECTORY": directory},
            clear=False,
        ):
            self.widget.populateAll(study=self.study)
            manager = self.widget._getRunManager(prompt=True)
            handle = manager.reserve_run(
                self.widget.state,
                "panel.in",
                ["/bin/true", "-f", "{input}"],
            )
            self.widget.refreshRunHistory(preferred_run_id=handle.run_id)
            self.assertTrue(self.widget.markInterruptedBtn.isEnabled())

            pathlib.Path(handle.input_file).write_text(
                "stale run input\n", encoding="utf-8"
            )
            manager.register_input(handle.run_id)
            manager.mark_running(handle.run_id, process_id=123)
            partial = pathlib.Path(handle.results_directory) / "partial.vtu"
            partial.write_text(
                "<VTKFile><UnstructuredGrid><Piece>"
                "<PointData><DataArray Name=\"Displacement\"/></PointData>"
                "</Piece></UnstructuredGrid></VTKFile>",
                encoding="utf-8",
            )

            class FakeLiveProcess:
                def __init__(self):
                    self.terminated = False
                    self.killed = False

                def state(self):
                    return QtCore.QProcess.Running

                def terminate(self):
                    self.terminated = True

                def kill(self):
                    self.killed = True

            process = FakeLiveProcess()
            self.widget._active_run = handle
            self.widget.solverProcess = process
            self.widget.refreshRunHistory(preferred_run_id=handle.run_id)

            self.assertFalse(self.widget.markInterruptedBtn.isEnabled())
            with unittest.mock.patch.object(
                QtWidgets.QMessageBox, "question"
            ) as question:
                self.assertFalse(self.widget.markSelectedRunInterrupted())
            question.assert_not_called()
            self.assertFalse(process.terminated)
            self.assertFalse(process.killed)
            self.assertEqual(manager.load_run(handle.run_id)["status"], "running")

            # A run left by a previous SALOME session is no longer represented
            # by this widget's QProcess and can be recovered explicitly.
            self.widget._active_run = None
            self.widget.solverProcess = None
            self.widget.refreshRunHistory(preferred_run_id=handle.run_id)
            self.assertTrue(self.widget.markInterruptedBtn.isEnabled())
            with unittest.mock.patch.object(
                QtWidgets.QMessageBox,
                "question",
                return_value=QtWidgets.QMessageBox.Yes,
            ):
                self.assertTrue(self.widget.markSelectedRunInterrupted())

            manifest = manager.load_run(handle.run_id, verify_files=True)
            self.assertEqual(manifest["status"], "failed")
            self.assertIn("marked interrupted", manifest["message"])
            self.assertEqual(
                [entry["path"] for entry in manifest["result_files"]],
                ["results/partial.vtu"],
            )
            self.assertEqual(partial.stat().st_mode & 0o222, 0)
            self.assertEqual(
                pathlib.Path(handle.results_directory).stat().st_mode & 0o222,
                0,
            )
            self.assertFalse(self.widget.markInterruptedBtn.isEnabled())
            self.assertIn("Status: failed", self.widget.runSummaryLabel.text())

            manager.delete_run(handle.run_id)

    def test_generate_and_run_records_qprocess_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory, unittest.mock.patch.dict(
            os.environ,
            {
                "OOFEM_RESULTS_DIRECTORY": directory,
                "OOFEM_AUTO_OPEN_PARAVIS": "false",
                "OOFEM_SOLVER_TIMEOUT": "5",
            },
            clear=False,
        ):
            fake_solver = pathlib.Path(directory) / "fake-oofem"
            fake_solver.write_text(
                """#!/usr/bin/env python3
import pathlib
import sys

if sys.argv[1:] == ["-v"]:
    print("OOFEM version 3.0 (fake); Branch: test; Hash: abc123")
    raise SystemExit(0)
source = pathlib.Path(sys.argv[2])
output = pathlib.Path(source.read_text(encoding="utf-8").splitlines()[0])
output.write_text("fake OOFEM output\\n", encoding="utf-8")
vtu = pathlib.Path(str(output) + ".m0.1.vtu")
vtu.write_text(
    '<VTKFile><UnstructuredGrid><Piece>'
    '<PointData><DataArray Name="Displacement"/></PointData>'
    '</Piece></UnstructuredGrid></VTKFile>',
    encoding="utf-8",
)
pvd = pathlib.Path(str(output) + ".m0.pvd")
pvd.write_text(
    '<VTKFile><Collection><DataSet timestep="1" file="{}"/>'
    '</Collection></VTKFile>'.format(vtu.name),
    encoding="utf-8",
)
print("0 error(s)")
""",
                encoding="utf-8",
            )
            fake_solver.chmod(fake_solver.stat().st_mode | 0o100)
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
                    "name": "sheet",
                    "oofem_type": "SimpleCS",
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
            self.widget.oofemExecutableEdit.setText(str(fake_solver))

            self.widget.runSolver()
            deadline = time.monotonic() + 5.0
            while (
                self.widget.solverProcess is not None
                and self.widget.solverProcess.state()
                != QtCore.QProcess.NotRunning
                and time.monotonic() < deadline
            ):
                QtWidgets.QApplication.processEvents()
                time.sleep(0.01)
            QtWidgets.QApplication.processEvents()

            self.assertEqual(
                self.widget.solverProcess.state(), QtCore.QProcess.NotRunning
            )
            run_id = self.widget.state["last_run_id"]
            manifest = self.widget._getRunManager().load_run(
                run_id, verify_files=True
            )
            self.assertEqual(manifest["status"], "succeeded")
            self.assertEqual(manifest["exit_code"], 0)
            self.assertEqual(len(manifest["result_files"]), 3)
            self.assertEqual(manifest["solver_log"]["path"], "solver.log")
            self.assertIn("OOFEM version 3.0", manifest["solver"]["version"])
            self.assertIn("0 error(s)", self.widget.solverLog.toPlainText())
            self.assertIn("time steps: 1", self.widget.runSummaryLabel.text())

            source_run_id = run_id
            self.assertEqual(
                self.widget.runHistoryCombo.currentData(), source_run_id
            )
            self.widget.rerunSelectedRun()
            self.assertNotEqual(
                self.widget.state["last_run_id"],
                source_run_id,
                msg=self.widget.solverLog.toPlainText(),
            )
            deadline = time.monotonic() + 5.0
            while (
                self.widget.solverProcess.state() != QtCore.QProcess.NotRunning
                and time.monotonic() < deadline
            ):
                QtWidgets.QApplication.processEvents()
                time.sleep(0.01)
            QtWidgets.QApplication.processEvents()

            rerun_id = self.widget.state["last_run_id"]
            self.assertNotEqual(rerun_id, source_run_id)
            rerun = self.widget._getRunManager().load_run(
                rerun_id, verify_files=True
            )
            self.assertEqual(rerun["status"], "succeeded")
            self.assertEqual(rerun["source_run_id"], source_run_id)
            self.assertEqual(self.widget.runHistoryCombo.count(), 2)

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
