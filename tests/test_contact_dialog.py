"""Offscreen tests for structural contact-pair editing."""

import os
import pathlib
import sys
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from OOFEMSalomePlugin.OOFEMQt import QtWidgets
    from OOFEMSalomePlugin.OOFEMContactDialog import OOFEMContactDialog

    _QT_IMPORT_ERROR = None
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
except Exception as error:  # pragma: no cover - environment without Qt
    QtWidgets = None
    OOFEMContactDialog = None
    _QT_IMPORT_ERROR = error


@unittest.skipUnless(
    QtWidgets is not None,
    "PyQt5 or PySide2 is required for contact dialog tests: {}".format(
        _QT_IMPORT_ERROR
    ),
)
class OOFEMContactDialogTests(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch.object(
            QtWidgets.QMessageBox,
            "warning",
            unittest.mock.Mock(return_value=QtWidgets.QMessageBox.Ok),
        )
        self.warning = patcher.start()
        self.addCleanup(patcher.stop)
        self.functions = [
            {
                "id": "ltf-1",
                "name": "Constant",
                "oofem_type": "ConstantFunction",
            }
        ]

    def test_builds_canonical_contact_pair(self):
        dialog = OOFEMContactDialog(
            ["MASTER", "SLAVE"], time_functions=self.functions
        )
        self.addCleanup(dialog.deleteLater)
        dialog.nameEdit.setText("interface")
        dialog.masterGroupCombo.setCurrentText("MASTER")
        dialog.slaveGroupCombo.setCurrentText("SLAVE")
        dialog.normalPenaltyEdit.setText("2e6")
        dialog.tangentialPenaltyEdit.setText("3e5")
        dialog.frictionEdit.setText("0.2")
        dialog.twoPassCheck.setChecked(True)

        data = dialog.get_data()

        self.assertEqual(data["master_group"], "MASTER")
        self.assertEqual(data["slave_group"], "SLAVE")
        self.assertEqual(data["time_function_id"], "ltf-1")
        self.assertEqual(data["params"]["normal_penalty"], 2.0e6)
        self.assertEqual(data["params"]["friction"], 0.2)
        self.assertTrue(data["params"]["two_pass"])

    def test_rejects_same_surface_and_nonfinite_penalty(self):
        dialog = OOFEMContactDialog(
            ["MASTER", "SLAVE"], time_functions=self.functions
        )
        self.addCleanup(dialog.deleteLater)
        dialog.masterGroupCombo.setCurrentText("MASTER")
        dialog.slaveGroupCombo.setCurrentText("MASTER")
        with self.assertRaisesRegex(ValueError, "must be different"):
            dialog.get_data()

        dialog.slaveGroupCombo.setCurrentText("SLAVE")
        dialog.normalPenaltyEdit.setText("nan")
        with self.assertRaisesRegex(ValueError, "must be finite"):
            dialog.get_data()

    def test_preserves_and_marks_missing_references(self):
        dialog = OOFEMContactDialog(
            ["CURRENT_MASTER", "CURRENT_SLAVE"],
            time_functions=self.functions,
            existing_contact={
                "name": "stale pair",
                "master_group": "OLD_MASTER",
                "slave_group": "OLD_SLAVE",
                "time_function_id": "old-ltf",
                "params": {},
            },
        )
        self.addCleanup(dialog.deleteLater)

        self.assertIn("missing", dialog.masterGroupCombo.currentText())
        self.assertIn("OLD_MASTER", dialog.masterGroupCombo.currentText())
        self.assertIn("missing", dialog.slaveGroupCombo.currentText())
        self.assertIn("OLD_SLAVE", dialog.slaveGroupCombo.currentText())
        self.assertIn("missing", dialog.timeFunctionCombo.currentText())
        self.assertIn("old-ltf", dialog.timeFunctionCombo.currentText())

        data = dialog.get_data()
        self.assertEqual(data["master_group"], "OLD_MASTER")
        self.assertEqual(data["slave_group"], "OLD_SLAVE")
        self.assertEqual(data["time_function_id"], "old-ltf")

        dialog.masterGroupCombo.setCurrentIndex(
            dialog.masterGroupCombo.findData("CURRENT_MASTER")
        )
        dialog.slaveGroupCombo.setCurrentIndex(
            dialog.slaveGroupCombo.findData("CURRENT_SLAVE")
        )
        dialog.timeFunctionCombo.setCurrentIndex(
            dialog.timeFunctionCombo.findData("ltf-1")
        )
        replacement = dialog.get_data()
        self.assertEqual(replacement["master_group"], "CURRENT_MASTER")
        self.assertEqual(replacement["slave_group"], "CURRENT_SLAVE")
        self.assertEqual(replacement["time_function_id"], "ltf-1")

    def test_parses_legacy_boolean_and_decimal_algorithm_values(self):
        dialog = OOFEMContactDialog(
            ["MASTER", "SLAVE"],
            time_functions=self.functions,
            existing_contact={
                "master_group": "MASTER",
                "slave_group": "SLAVE",
                "params": {
                    "algorithm": "1.0",
                    "two_pass": "false",
                    "reverse_master": "0",
                    "reverse_slave": "yes",
                },
            },
        )
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.algorithmCombo.currentData(), 1)
        self.assertFalse(dialog.twoPassCheck.isChecked())
        self.assertFalse(dialog.reverseMasterCheck.isChecked())
        self.assertTrue(dialog.reverseSlaveCheck.isChecked())
        data = dialog.get_data()
        self.assertEqual(data["params"]["algorithm"], 1)
        self.assertFalse(data["params"]["two_pass"])

    def test_run_allows_editing_when_current_groups_are_missing(self):
        existing = {
            "master_group": "OLD_MASTER",
            "slave_group": "OLD_SLAVE",
            "params": {},
        }
        with unittest.mock.patch.object(
            OOFEMContactDialog,
            "exec_",
            return_value=QtWidgets.QDialog.Rejected,
        ):
            result = OOFEMContactDialog.run(
                [], existing_contact=existing, parent=None
            )

        self.assertIsNone(result)
        self.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
