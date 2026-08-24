"""Offscreen tests for canonical multi-component boundary conditions."""

import os
import pathlib
import sys
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


try:
    from OOFEMSalomePlugin.OOFEMQt import Qt, QtWidgets

    _QT_IMPORT_ERROR = None
except Exception as error:  # pragma: no cover - environment without Qt
    Qt = None
    QtWidgets = None
    _QT_IMPORT_ERROR = error


if QtWidgets is not None:
    from OOFEMSalomePlugin.OOFEMBCDialog import OOFEMBCDialog
    from OOFEMSalomePlugin.OOFEMConfig import (
        load_boundary_condition_templates,
    )

    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@unittest.skipUnless(
    QtWidgets is not None,
    "PyQt5 or PySide2 is required for BC dialog tests: {}".format(
        _QT_IMPORT_ERROR
    ),
)
class BoundaryConditionDialogV2Tests(unittest.TestCase):
    def setUp(self):
        warning_patch = unittest.mock.patch.object(
            QtWidgets.QMessageBox,
            "warning",
            unittest.mock.Mock(return_value=QtWidgets.QMessageBox.Ok),
        )
        self.warning = warning_patch.start()
        self.addCleanup(warning_patch.stop)
        self.templates = load_boundary_condition_templates()
        self.groups = {
            "nodes": ["FIXED", "LOADED"],
            "boundaries": ["EDGE"],
            "elements": ["DOMAIN"],
        }
        self.functions = [
            {
                "id": "ltf-constant",
                "name": "Constant",
                "oofem_type": "constantfunction",
                "params": {"f(t)": 1.0},
            },
            {
                "id": "ltf-ramp",
                "name": "Ramp",
                "oofem_type": "piecewiselinfunction",
                "params": {"t": [0.0, 1.0], "f(t)": [0.0, 1.0]},
            },
        ]

    def _dialog(self, existing=None):
        dialog = OOFEMBCDialog(
            self.templates,
            self.groups,
            existing_bc=existing,
            time_functions=self.functions,
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    @staticmethod
    def _set_parameter(dialog, key, text):
        for row in range(dialog.parameterTable.rowCount()):
            item = dialog.parameterTable.item(row, 0)
            if item.data(Qt.UserRole) == key:
                dialog.parameterTable.item(row, 1).setText(text)
                return
        raise AssertionError("Missing parameter '{}'".format(key))

    def test_creates_multi_component_load_with_time_reference(self):
        dialog = self._dialog()
        dialog.nameEdit.setText("force")
        dialog.groupCombo.setCurrentText("LOADED")
        dialog.timeFunctionCombo.setCurrentIndex(1)
        self._set_parameter(dialog, "dofs", "1, 2")
        self._set_parameter(dialog, "components", "5, -3")

        dialog.accept()

        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(
            dialog.get_data(),
            {
                "name": "force",
                "oofem_type": "NodalLoad",
                "assigned_group": "LOADED",
                "time_function_id": "ltf-ramp",
                "params": {
                    "dofs": [1, 2],
                    "components": [5.0, -3.0],
                },
            },
        )

    def test_legacy_scalar_record_is_edited_as_canonical_arrays(self):
        existing = {
            "name": "fixed",
            "oofem_type": "Displacement",
            "assigned_group": "FIXED",
            "time_function_id": "ltf-constant",
            "params": {"dof": 2, "val": 0.0},
        }
        dialog = self._dialog(existing)
        self.assertEqual(dialog.parameterTable.item(0, 1).text(), "2")
        self.assertEqual(dialog.parameterTable.item(1, 1).text(), "0.0")
        self.assertEqual(
            dialog.get_data()["params"],
            {"dofs": [2], "values": [0.0]},
        )

    def test_surface_load_offers_only_boundary_groups(self):
        dialog = self._dialog()
        surface_index = next(
            index
            for index, template in enumerate(self.templates)
            if template["oofem_name"] == "SurfaceLoad"
        )
        dialog.typeCombo.setCurrentIndex(surface_index)
        self.assertEqual(
            [
                dialog.groupCombo.itemText(index)
                for index in range(dialog.groupCombo.count())
            ],
            ["<None>", "EDGE"],
        )

    def test_rejects_mismatched_or_duplicate_components(self):
        dialog = self._dialog()
        dialog.nameEdit.setText("bad")
        dialog.groupCombo.setCurrentText("LOADED")
        self._set_parameter(dialog, "dofs", "1, 2")
        self._set_parameter(dialog, "components", "1")
        dialog.accept()
        self.assertNotEqual(dialog.result(), QtWidgets.QDialog.Accepted)

        self._set_parameter(dialog, "dofs", "1, 1")
        self._set_parameter(dialog, "components", "1, 2")
        dialog.accept()
        self.assertNotEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(self.warning.call_count, 2)


if __name__ == "__main__":
    unittest.main()
