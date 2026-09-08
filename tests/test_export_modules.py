"""Unit tests for OOFEM export modules customization and variable catalog."""

import os
import sys
import types
import unittest

REPOSITORY_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPOSITORY_ROOT, "src"))


class EnumItem:
    def __init__(self, name, value):
        self._n = name
        self._v = value


ENTITY_ITEMS = [
    EnumItem("Entity_Edge", 1),
    EnumItem("Entity_Quad_Edge", 2),
    EnumItem("Entity_Triangle", 3),
    EnumItem("Entity_Quad_Triangle", 4),
    EnumItem("Entity_Hexa", 5),
    EnumItem("Entity_Quad_Hexa", 6),
    EnumItem("Entity_Quadrangle", 7),
    EnumItem("Entity_Tetra", 8),
    EnumItem("Entity_Quad_Quadrangle", 9),
]

# Stubs installed here leak into every test file that runs after this one, so
# note what we added and take it back in tearDownModule().  Without that,
# test_module_sessions.py imports this module's placeholder OOFEMModule instead
# of the real one and every one of its tests dies on
# "module OOFEMSalomePlugin.OOFEMModule has no attribute OOFEMModule" -- but
# only when the two files run in the same session, which is why the file passes
# on its own.
_INSTALLED_STUBS = []

if "SMESH" not in sys.modules:
    fake_smesh = types.ModuleType("SMESH")
    fake_smesh.NODE = 0
    fake_smesh.EDGE = 1
    fake_smesh.FACE = 2
    fake_smesh.VOLUME = 3
    fake_smesh.EntityType = types.SimpleNamespace(_items=ENTITY_ITEMS)
    sys.modules["SMESH"] = fake_smesh
    _INSTALLED_STUBS.append("SMESH")

if "OOFEMSalomePlugin.OOFEMModule" not in sys.modules:
    fake_module = types.ModuleType("OOFEMSalomePlugin.OOFEMModule")
    fake_module.getModule = lambda: None
    sys.modules["OOFEMSalomePlugin.OOFEMModule"] = fake_module
    _INSTALLED_STUBS.append("OOFEMSalomePlugin.OOFEMModule")

from OOFEMSalomePlugin.OOFEMConfig import (
    load_export_variable_catalog,
    solver_settings,
)
from OOFEMSalomePlugin.OOFEMExportCatalog import (
    get_primary_variables,
    get_internal_variables,
    get_variable_categories,
    describe_variable,
    format_vtk_record,
    parse_vtk_record,
    default_variables_for_preset,
)
from OOFEMSalomePlugin.OOFEMExporter import OOFEMExporter
from OOFEMSalomePlugin.OOFEMProject import (
    new_project_state,
    migrate_project_state,
)

# The stubs above exist only so the imports in this block can run.  They are
# removed immediately, not in tearDownModule(), because pytest IMPORTS every
# test file during collection: test_module_sessions.py binds
# OOFEMSalomePlugin.OOFEMModule at its own import time, so a stub still present
# then is what it gets for the rest of the session, and all of its tests fail
# with "has no attribute OOFEMModule". A teardown would run long after that.
for _name in _INSTALLED_STUBS:
    sys.modules.pop(_name, None)


class ExportModulesCatalogTests(unittest.TestCase):
    def test_catalog_structure(self):
        catalog = load_export_variable_catalog()
        self.assertIn("categories", catalog)
        self.assertIn("primary_variables", catalog)
        self.assertIn("internal_variables", catalog)

        primary = get_primary_variables()
        self.assertTrue(any(v["id"] == 1 and v["name"] == "DisplacementVector" for v in primary))

        internal = get_internal_variables()
        self.assertTrue(any(v["id"] == 1 and v["name"] == "IST_StressTensor" for v in internal))
        self.assertTrue(any(v["id"] == 4 and v["name"] == "IST_StrainTensor" for v in internal))
        self.assertTrue(any(v["id"] == 81 and v["name"] == "IST_vonMisesStress" for v in internal))
        self.assertTrue(any(v["id"] == 150 and v["name"] == "IST_ContactGap" for v in internal))

    def test_describe_variable(self):
        desc_prim = describe_variable("primvars", 1)
        self.assertEqual(desc_prim["name"], "DisplacementVector")
        self.assertEqual(desc_prim["id"], 1)
        self.assertEqual(desc_prim["category"], "primvars")

        desc_internal = describe_variable("cellvars", 81)
        self.assertEqual(desc_internal["name"], "IST_vonMisesStress")
        self.assertEqual(desc_internal["id"], 81)
        self.assertEqual(desc_internal["category"], "cellvars")

        desc_custom = describe_variable("vars", 9999)
        self.assertEqual(desc_custom["id"], 9999)
        self.assertIn("9999", desc_custom["description"])

    def test_format_vtk_record(self):
        vars_list = [
            {"category": "primvars", "id": 1},
            {"category": "vars", "id": 1},
            {"category": "vars", "id": 4},
            {"category": "cellvars", "id": 81},
            {"category": "ipvars", "id": 27},
        ]
        record = format_vtk_record(vars_list)
        self.assertEqual(
            record,
            "vtkxml tstep_all domain_all primvars 1 1 vars 2 1 4 cellvars 1 81 ipvars 1 27",
        )

    def test_parse_vtk_record(self):
        raw = "vtkxml tstep_all domain_all primvars 1 1 vars 2 1 4 cellvars 1 81"
        parsed = parse_vtk_record(raw)
        self.assertEqual(len(parsed), 4)
        self.assertEqual(parsed[0]["category"], "primvars")
        self.assertEqual(parsed[0]["id"], 1)
        self.assertEqual(parsed[0]["name"], "DisplacementVector")

        self.assertEqual(parsed[1]["category"], "vars")
        self.assertEqual(parsed[1]["id"], 1)
        self.assertEqual(parsed[1]["name"], "IST_StressTensor")

        self.assertEqual(parsed[2]["category"], "vars")
        self.assertEqual(parsed[2]["id"], 4)
        self.assertEqual(parsed[2]["name"], "IST_StrainTensor")

        self.assertEqual(parsed[3]["category"], "cellvars")
        self.assertEqual(parsed[3]["id"], 81)
        self.assertEqual(parsed[3]["name"], "IST_vonMisesStress")

    def test_default_variables_for_preset(self):
        vtk_vars = default_variables_for_preset("vtk")
        self.assertEqual(len(vtk_vars), 2)
        self.assertEqual(vtk_vars[0]["category"], "primvars")
        self.assertEqual(vtk_vars[1]["category"], "cellvars")

        contact_vars = default_variables_for_preset("contact-vtk")
        self.assertEqual(len(contact_vars), 5)
        contact_ids = [v["id"] for v in contact_vars if v["category"] == "cellvars"]
        self.assertIn(150, contact_ids)
        self.assertIn(151, contact_ids)
        self.assertIn(152, contact_ids)

        text_vars = default_variables_for_preset("text-only")
        self.assertEqual(text_vars, [])

    def test_solver_settings_with_variables(self):
        custom_vars = [
            {"category": "primvars", "id": 1},
            {"category": "vars", "id": 4},
        ]
        settings = solver_settings(preset_id="custom", variables=custom_vars)
        self.assertTrue(settings["vtk"])
        self.assertEqual(
            settings["vtk_record"],
            "vtkxml tstep_all domain_all primvars 1 1 vars 1 4",
        )

        text_settings = solver_settings(preset_id="text-only")
        self.assertFalse(text_settings["vtk"])

    def test_exporter_validation_extended_records(self):
        # Valid record with all 4 categories
        valid_record = "vtkxml tstep_all domain_all primvars 1 1 vars 2 1 4 cellvars 1 81 ipvars 1 27"
        errors = OOFEMExporter._validate_vtk_record(valid_record)
        self.assertEqual(errors, [])

        # Invalid count in vars
        bad_record = "vtkxml tstep_all domain_all primvars 1 1 vars 3 1 4"
        errors = OOFEMExporter._validate_vtk_record(bad_record)
        self.assertTrue(any("declares 3 'vars'" in e for e in errors))

        # Invalid non-integer ID in cellvars
        bad_id_record = "vtkxml tstep_all domain_all cellvars 1 abc"
        errors = OOFEMExporter._validate_vtk_record(bad_id_record)
        self.assertTrue(any("cellvars" in e for e in errors))

    def test_project_state_migration(self):
        state = new_project_state()
        self.assertIn("export_variables", state)
        self.assertEqual(len(state["export_variables"]), 2)

        # Migrating older state without export_variables
        legacy_state = {
            "schema_version": 4,
            "solver_preset": "contact-vtk",
        }
        migrated = migrate_project_state(legacy_state)
        self.assertIn("export_variables", migrated)
        self.assertEqual(len(migrated["export_variables"]), 5)

    def test_sync_oofem_enums_parser(self):
        sys.path.insert(0, os.path.join(REPOSITORY_ROOT, "scripts"))
        import sync_oofem_enums

        sample_unknown_content = """
        #define UnknownType_DEF \\
            ENUM_ITEM_WITH_VALUE(DisplacementVector, 1) \\
            ENUM_ITEM_WITH_VALUE(VelocityVector, 4)
        """
        sample_internal_content = """
        #define InternalStateType_DEF \\
            ENUM_ITEM_WITH_VALUE(IST_StressTensor, 1) \\
            ENUM_ITEM_WITH_VALUE(IST_StrainTensor, 4) \\
            ENUM_ITEM_WITH_VALUE(IST_vonMisesStress, 81)
        """
        catalog = sync_oofem_enums.build_catalog(
            sample_unknown_content, sample_internal_content
        )
        self.assertEqual(len(catalog["primary_variables"]), 2)
        # 3 parsed + 3 contact defaults (150, 151, 152) = 6
        self.assertEqual(len(catalog["internal_variables"]), 6)
        self.assertEqual(catalog["primary_variables"][0]["name"], "DisplacementVector")
        self.assertEqual(catalog["internal_variables"][0]["name"], "IST_StressTensor")


if __name__ == "__main__":
    unittest.main()

