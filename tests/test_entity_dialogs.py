"""Offscreen tests for cross-section and time-function dialogs."""

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
    from OOFEMSalomePlugin.OOFEMCrossSectionDialog import (
        OOFEMCrossSectionDialog,
    )
    from OOFEMSalomePlugin.OOFEMTimeFunctionDialog import (
        OOFEMTimeFunctionDialog,
    )
    from OOFEMSalomePlugin.OOFEMMaterialDialog import OOFEMMaterialDialog

    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


CROSS_SECTION_TEMPLATES = [
    {
        "display_name": "Detailed",
        "oofem_name": "simplecs",
        "params": [
            {"key": "area", "name": "Area", "type": "float", "default": 1.5},
            {"key": "order", "name": "Order", "type": "int", "default": 2},
            {
                "key": "label",
                "name": "Label",
                "type": "string",
                "default": "beam",
            },
            {
                "key": "stations",
                "name": "Stations",
                "type": "float_list",
                "default": [0.0, 1.0],
            },
            {
                "key": "density",
                "name": "Density",
                "type": "float",
                "default": 7.8,
                "optional": True,
            },
        ],
    },
    {"display_name": "No parameters", "oofem_name": "emptycs", "params": []},
]

TIME_FUNCTION_TEMPLATES = [
    {
        "display_name": "Constant",
        "oofem_name": "constantfunction",
        "params": [
            {"key": "f(t)", "name": "Value", "type": "float", "default": 1.0}
        ],
    },
    {
        "display_name": "Piecewise",
        "oofem_name": "piecewiselinfunction",
        "params": [
            {
                "key": "t",
                "name": "Times",
                "type": "float_list",
                "default": [0.0, 1.0],
            },
            {
                "key": "f(t)",
                "name": "Values",
                "type": "float_list",
                "default": [0.0, 1.0],
            },
        ],
    },
]


@unittest.skipUnless(
    QtWidgets is not None,
    "PyQt5 or PySide2 is required for entity-dialog tests: {}".format(
        _QT_IMPORT_ERROR
    ),
)
class EntityDialogTests(unittest.TestCase):
    def setUp(self):
        warning_patch = unittest.mock.patch.object(
            QtWidgets.QMessageBox,
            "warning",
            unittest.mock.Mock(return_value=QtWidgets.QMessageBox.Ok),
        )
        self.warning = warning_patch.start()
        self.addCleanup(warning_patch.stop)

    def _track(self, dialog):
        self.addCleanup(dialog.deleteLater)
        return dialog

    @staticmethod
    def _parameter_row(dialog, key):
        for row in range(dialog.parameterTable.rowCount()):
            if dialog.parameterTable.item(row, 0).data(Qt.UserRole) == key:
                return row
        raise AssertionError("No parameter row for {!r}".format(key))

    @classmethod
    def _set_parameter(cls, dialog, key, value):
        row = cls._parameter_row(dialog, key)
        dialog.parameterTable.item(row, 1).setText(value)

    @staticmethod
    def _override_row(dialog, salome_type):
        for row in range(dialog.overrideTable.rowCount()):
            if dialog.overrideTable.item(row, 0).text() == salome_type:
                return row
        raise AssertionError("No override row for {!r}".format(salome_type))

    def _cross_section(self, existing=None, materials=None, groups=None):
        return self._track(
            OOFEMCrossSectionDialog(
                CROSS_SECTION_TEMPLATES,
                materials
                if materials is not None
                else [
                    {"id": "mat-1", "name": "Steel"},
                    {"id": "mat-2", "name": "Concrete"},
                ],
                groups if groups is not None else ["BEAMS", "SHELLS"],
                {"Triangle", "Edge"},
                existing,
            )
        )

    def _piecewise(self):
        dialog = self._track(
            OOFEMTimeFunctionDialog(TIME_FUNCTION_TEMPLATES)
        )
        dialog.typeCombo.setCurrentIndex(1)
        dialog.nameEdit.setText("load history")
        return dialog

    def test_cross_section_create_coerces_all_types_and_builds_overrides(self):
        dialog = self._cross_section()
        self.assertEqual(dialog.parameterTable.columnCount(), 2)
        self.assertEqual(dialog.parameterTable.rowCount(), 5)
        self.assertEqual(
            [
                dialog.overrideTable.item(row, 0).text()
                for row in range(dialog.overrideTable.rowCount())
            ],
            ["Edge", "Triangle"],
        )

        dialog.nameEdit.setText(" section ")
        self._set_parameter(dialog, "area", "2.75")
        self._set_parameter(dialog, "order", "3")
        self._set_parameter(dialog, "label", "custom")
        self._set_parameter(dialog, "stations", "0, 0.25, 1")
        self._set_parameter(dialog, "density", "")
        dialog.overrideGroup.setChecked(True)
        edge_row = self._override_row(dialog, "Edge")
        dialog.overrideTable.item(edge_row, 1).setText("Beam2d")

        dialog.accept()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(
            dialog.get_data(),
            {
                "name": "section",
                "oofem_type": "simplecs",
                "material_id": "mat-1",
                "assigned_group": "BEAMS",
                "element_options": {"nlgeo": "inherit"},
                "element_mapping_override": {"Edge": "Beam2d"},
                "params": {
                    "area": 2.75,
                    "order": 3,
                    "label": "custom",
                    "stations": [0.0, 0.25, 1.0],
                },
            },
        )

    def test_cross_section_edit_restores_references_params_and_overrides(self):
        dialog = self._cross_section(
            {
                "name": "edited",
                "oofem_type": "simplecs",
                "material_id": "mat-2",
                "assigned_group": "SHELLS",
                "element_options": {
                    "nlgeo": "on",
                    "vendor_element_option": 7,
                },
                "element_mapping_override": {"Legacy": "LegacyElement"},
                "params": {
                    "area": 4.0,
                    "order": 5,
                    "label": "old",
                    "stations": [0.0, 2.0],
                },
            }
        )

        self.assertEqual(dialog.nameEdit.text(), "edited")
        self.assertEqual(dialog.materialCombo.currentData(), "mat-2")
        self.assertEqual(dialog.groupCombo.currentText(), "SHELLS")
        self.assertEqual(dialog.nlgeoCombo.currentData(), "on")
        self.assertEqual(
            dialog.parameterTable.item(
                self._parameter_row(dialog, "stations"), 1
            ).text(),
            "0.0, 2.0",
        )
        self.assertEqual(
            dialog.parameterTable.item(
                self._parameter_row(dialog, "density"), 1
            ).text(),
            "",
        )
        self.assertIn(
            "Legacy",
            [
                dialog.overrideTable.item(row, 0).text()
                for row in range(dialog.overrideTable.rowCount())
            ],
        )
        self.assertNotIn("density", dialog.get_data()["params"])
        self.assertEqual(
            dialog.get_data()["element_mapping_override"],
            {"Legacy": "LegacyElement"},
        )
        dialog.nlgeoCombo.setCurrentIndex(
            dialog.nlgeoCombo.findData("off")
        )
        self.assertEqual(
            dialog.get_data()["element_options"],
            {"nlgeo": "off", "vendor_element_option": 7},
        )

    def test_cross_section_rejects_empty_name_missing_references_and_bad_value(self):
        dialog = self._cross_section()
        dialog.accept()
        self.assertNotEqual(dialog.result(), QtWidgets.QDialog.Accepted)

        missing_material = self._cross_section(
            materials=[{"id": None, "name": "Broken"}]
        )
        missing_material.nameEdit.setText("section")
        missing_material.accept()
        self.assertNotEqual(
            missing_material.result(), QtWidgets.QDialog.Accepted
        )

        missing_group = self._cross_section(groups=[])
        missing_group.nameEdit.setText("section")
        missing_group.accept()
        self.assertNotEqual(missing_group.result(), QtWidgets.QDialog.Accepted)

        stale_reference = self._cross_section(
            existing={
                "name": "stale",
                "oofem_type": "simplecs",
                "material_id": "missing-material",
                "assigned_group": "BEAMS",
                "params": {},
            }
        )
        stale_reference.accept()
        self.assertNotEqual(
            stale_reference.result(), QtWidgets.QDialog.Accepted
        )

        bad_value = self._cross_section()
        bad_value.nameEdit.setText("section")
        self._set_parameter(bad_value, "order", "1.5")
        bad_value.accept()
        self.assertNotEqual(bad_value.result(), QtWidgets.QDialog.Accepted)

        invalid_nlgeo = self._cross_section(
            existing={
                "name": "invalid nlgeo",
                "oofem_type": "simplecs",
                "material_id": "mat-1",
                "assigned_group": "BEAMS",
                "element_options": {"nlgeo": "sometimes"},
                "params": {},
            }
        )
        self.assertTrue(invalid_nlgeo.nlgeoCombo.currentText().startswith("[invalid]"))
        invalid_nlgeo.accept()
        self.assertNotEqual(
            invalid_nlgeo.result(), QtWidgets.QDialog.Accepted
        )
        self.assertEqual(self.warning.call_count, 6)

    def test_time_function_switches_templates_uses_defaults_and_supports_edit(self):
        dialog = self._track(
            OOFEMTimeFunctionDialog(TIME_FUNCTION_TEMPLATES)
        )
        self.assertEqual(dialog.parameterTable.rowCount(), 1)
        self.assertEqual(dialog.parameterTable.item(0, 1).text(), "1.0")

        dialog.typeCombo.setCurrentIndex(1)
        self.assertEqual(dialog.parameterTable.rowCount(), 2)
        dialog.nameEdit.setText("ramp")
        self._set_parameter(dialog, "t", "0, 0.5, 2")
        self._set_parameter(dialog, "f(t)", "0, 1, 4")
        dialog.accept()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(
            dialog.get_data(),
            {
                "name": "ramp",
                "oofem_type": "piecewiselinfunction",
                "params": {
                    "t": [0.0, 0.5, 2.0],
                    "f(t)": [0.0, 1.0, 4.0],
                },
            },
        )

        edited = self._track(
            OOFEMTimeFunctionDialog(
                TIME_FUNCTION_TEMPLATES,
                {
                    "name": "existing",
                    "oofem_type": "piecewiselinfunction",
                    "params": {"t": [1.0, 3.0], "f(t)": [2.0, 8.0]},
                },
            )
        )
        self.assertEqual(edited.typeCombo.currentIndex(), 1)
        self.assertEqual(
            edited.parameterTable.item(
                self._parameter_row(edited, "t"), 1
            ).text(),
            "1.0, 3.0",
        )
        self.assertEqual(edited.get_data()["params"]["f(t)"], [2.0, 8.0])

    def test_time_function_validation_does_not_accept_invalid_piecewise_data(self):
        scenarios = [
            ("0, 1", "0", "different lengths"),
            ("0", "0", "fewer than two points"),
            ("0, 0, 1", "0, 1, 2", "duplicate time"),
            ("1, 0", "0, 1", "decreasing time"),
            ("nan, 1", "0, 1", "non-orderable time"),
            ("0, bad", "0, 1", "invalid float"),
        ]
        for times, values, label in scenarios:
            with self.subTest(label=label):
                dialog = self._piecewise()
                self._set_parameter(dialog, "t", times)
                self._set_parameter(dialog, "f(t)", values)
                dialog.accept()
                self.assertNotEqual(
                    dialog.result(), QtWidgets.QDialog.Accepted
                )

        unnamed = self._track(
            OOFEMTimeFunctionDialog(TIME_FUNCTION_TEMPLATES)
        )
        unnamed.accept()
        self.assertNotEqual(unnamed.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(self.warning.call_count, len(scenarios) + 1)


MATERIAL_TEMPLATES = [
    {
        "display_name": "3D Isotropic Elastic",
        "oofem_name": "ElasticIsotropic3d",
        "params": [
            {"key": "E", "name": "Young's Modulus", "type": "float", "default": 1.0},
            {"key": "nu", "name": "Poisson's Ratio", "type": "float", "default": 0.0},
            {"key": "d", "name": "Density", "type": "float", "default": 0.0},
        ],
    },
    {
        "display_name": "2D Isotropic Elastic (Plane Stress)",
        "oofem_name": "ElasticIsotropic2d",
        "params": [
            {"key": "E", "name": "Young's Modulus", "type": "float", "default": 1.0},
            {"key": "nu", "name": "Poisson's Ratio", "type": "float", "default": 0.0},
            {"key": "t", "name": "Thickness", "type": "float", "default": 1.0},
            {"key": "d", "name": "Density", "type": "float", "default": 0.0},
        ],
    },
    {
        "display_name": "1D Truss",
        "oofem_name": "Truss",
        "params": [
            {"key": "E", "name": "Young's Modulus", "type": "float", "default": 1.0},
            {"key": "A", "name": "Area", "type": "float", "default": 1.0},
        ],
    },
]

MATERIAL_LIBRARY = [
    {
        "id": "elasticisotropic-e210",
        "display_name": "ElasticIsotropic (E=210 GPa, nu=0.30, rho=7850 kg/m3)",
        "compatible_templates": ["ElasticIsotropic3d", "ElasticIsotropic2d"],
        "params": {"E": 210000000000.0, "nu": 0.3, "d": 7850.0},
    },
    {
        "id": "truss-e210",
        "display_name": "Truss (E=210 GPa, A=0.01 m2)",
        "compatible_templates": ["Truss"],
        "params": {"E": 210000000000.0, "A": 0.01, "d": 7850.0},
    },
]


@unittest.skipUnless(
    QtWidgets is not None,
    "PyQt5 or PySide2 is required for entity-dialog tests: {}".format(
        _QT_IMPORT_ERROR
    ),
)
class MaterialDialogLibraryPresetTests(unittest.TestCase):
    """A preset usable by more than one OOFEM material type is the same
    physical material regardless of which type backs it (e.g. isotropic
    elasticity doesn't change between a 2D and a 3D element) -- these tests
    lock in that a single 'compatible_templates' preset applies its shared
    params to whichever of those types is selected, without forcing one."""

    def _track(self, dialog):
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _material(self):
        return self._track(
            OOFEMMaterialDialog(
                MATERIAL_TEMPLATES,
                {"elements": ["BARS", "FACES"]},
                MATERIAL_LIBRARY,
            )
        )

    def _select_type(self, dialog, oofem_name):
        index = next(
            i
            for i, template in enumerate(MATERIAL_TEMPLATES)
            if template["oofem_name"] == oofem_name
        )
        dialog.typeCombo.setCurrentIndex(index)

    def _select_library(self, dialog, library_id):
        index = dialog.libraryCombo.findData(library_id)
        dialog.libraryCombo.setCurrentIndex(index)

    def test_generic_elastic_preset_applies_under_3d_type(self):
        dialog = self._material()
        self._select_type(dialog, "ElasticIsotropic3d")
        self._select_library(dialog, "elasticisotropic-e210")

        data = dialog.get_data()
        self.assertEqual(data["oofem_type"], "ElasticIsotropic3d")
        self.assertEqual(data["params"]["E"], 210000000000.0)
        self.assertEqual(data["params"]["nu"], 0.3)
        self.assertEqual(data["params"]["d"], 7850.0)

    def test_generic_elastic_preset_applies_under_2d_type_without_forcing_thickness(self):
        dialog = self._material()
        self._select_type(dialog, "ElasticIsotropic2d")
        self._select_library(dialog, "elasticisotropic-e210")

        data = dialog.get_data()
        self.assertEqual(data["oofem_type"], "ElasticIsotropic2d")
        self.assertEqual(data["params"]["E"], 210000000000.0)
        self.assertEqual(data["params"]["nu"], 0.3)
        # Thickness isn't part of "the material": the preset doesn't carry
        # it, so the 2D template's own default is used, not silently
        # dropped or borrowed from an unrelated dimension.
        self.assertEqual(data["params"]["t"], 1.0)

    def test_selecting_preset_does_not_override_an_already_compatible_type(self):
        dialog = self._material()
        self._select_type(dialog, "ElasticIsotropic2d")
        self._select_library(dialog, "elasticisotropic-e210")

        self.assertEqual(
            MATERIAL_TEMPLATES[dialog.typeCombo.currentIndex()]["oofem_name"],
            "ElasticIsotropic2d",
        )

    def test_selecting_preset_switches_an_incompatible_type_to_the_first_match(self):
        dialog = self._material()
        self._select_type(dialog, "Truss")
        self._select_library(dialog, "elasticisotropic-e210")

        self.assertEqual(
            MATERIAL_TEMPLATES[dialog.typeCombo.currentIndex()]["oofem_name"],
            "ElasticIsotropic3d",
        )

    def test_truss_preset_only_applies_under_truss_type(self):
        dialog = self._material()
        self._select_library(dialog, "truss-e210")
        self._select_type(dialog, "ElasticIsotropic3d")

        data = dialog.get_data()
        self.assertEqual(data["oofem_type"], "ElasticIsotropic3d")
        # The truss preset's params (E, A, d) must not leak into an
        # unrelated, incompatible type merely because it's selected.
        self.assertNotIn("A", data["params"])
        self.assertNotEqual(data["params"]["E"], 210000000000.0)


if __name__ == "__main__":
    unittest.main()
