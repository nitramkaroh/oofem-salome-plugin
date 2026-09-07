"""Headless (offscreen Qt) tests for OOFEMMainWidget.

These are the only tests that exercise the actual Qt widgets; everything
else in this suite fakes SALOME/Qt away entirely so it can run without
PyQt5/PySide2 installed. This module needs a real Qt binding, so it skips
itself when one is unavailable instead of failing the whole suite.
"""

import os
import pathlib
import sys
import types
import unittest
import unittest.mock

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class EnumItem:
    def __init__(self, name, value):
        self._n = name
        self._v = value


_ENTITY_ITEMS = [
    EnumItem("Entity_Edge", 1),
    EnumItem("Entity_Triangle", 3),
    EnumItem("Entity_Quadrangle", 7),
    EnumItem("Entity_Tetra", 8),
]


def _make_fake_smesh():
    module = types.ModuleType("SMESH")
    module.NODE = 0
    module.EDGE = 1
    module.FACE = 2
    module.VOLUME = 3
    module.EntityType = types.SimpleNamespace(_items=_ENTITY_ITEMS)
    return module


# OOFEMExporter caches whichever "SMESH" module was live at its first import
# (e.g. from test_exporter.py during discovery). Only plant a fake if nothing
# has imported it yet; otherwise reuse whatever is already bound so the two
# test modules can never disagree about entity-type identities.
if "OOFEMSalomePlugin.OOFEMExporter" not in sys.modules:
    sys.modules.setdefault("SMESH", _make_fake_smesh())

import OOFEMSalomePlugin.OOFEMExporter as _exporter_module  # noqa: E402

SMESH = _exporter_module.SMESH


def _entity_item(name):
    return next(item for item in SMESH.EntityType._items if item._n == name)


try:
    from OOFEMSalomePlugin.OOFEMQt import QtWidgets
    _QT_IMPORT_ERROR = None
except Exception as error:  # pragma: no cover - environment without Qt bindings
    QtWidgets = None
    _QT_IMPORT_ERROR = error

if QtWidgets is not None:
    from OOFEMSalomePlugin.OOFEMMainWidget import OOFEMMainWidget
    from OOFEMSalomePlugin.OOFEMCrossSectionDialog import OOFEMCrossSectionDialog

    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeGroup:
    def __init__(self, name, entity_type, entity_ids):
        self.name = name
        self.entity_type = entity_type
        self.entity_ids = list(entity_ids)

    def GetName(self):
        return self.name

    def GetType(self):
        return self.entity_type

    def GetIDs(self):
        return self.entity_ids


class FakeMesh:
    """One quadrangle sheet: a face group, a fixed-node group, a loaded edge."""

    def __init__(self):
        self.groups = [
            FakeGroup("MAT_FACES", SMESH.FACE, [100]),
            FakeGroup("BC_FIXED", SMESH.NODE, [10, 40]),
            FakeGroup("LOAD_EDGE", SMESH.EDGE, [500]),
        ]
        self._connectivity = {100: [10, 20, 30, 40], 500: [20, 30]}
        self._element_types = {100: _entity_item("Entity_Quadrangle")}
        self._coordinates = {
            10: (0.0, 0.0, 0.0),
            20: (1.0, 0.0, 0.0),
            30: (1.0, 1.0, 0.0),
            40: (0.0, 1.0, 0.0),
        }

    def GetGroups(self):
        return self.groups

    def GetNodesId(self):
        return list(self._coordinates)

    def GetNodeXYZ(self, node_id):
        return self._coordinates[node_id]

    def GetElementGeomType(self, element_id):
        return self._element_types[element_id]

    def GetElemNodes(self, element_id, *unused):
        return self._connectivity[element_id]

    def NbElements(self):
        return len(self._element_types)


class FakeStudyObject:
    def __init__(self, object_id, name, obj):
        self._id = object_id
        self._name = name
        self._obj = obj

    def GetObject(self):
        return self._obj

    def GetName(self):
        return self._name

    def GetID(self):
        return self._id


class FakeIterator:
    def __init__(self, items, recursive_items=None):
        self._items = items
        self._recursive_items = recursive_items
        self._index = 0

    def InitEx(self, all_levels):
        if all_levels and self._recursive_items is not None:
            self._items = self._recursive_items
            self._index = 0

    def More(self):
        return self._index < len(self._items)

    def Value(self):
        return self._items[self._index]

    def Next(self):
        self._index += 1


class FakeStudy:
    def __init__(self, mesh_objects, recursive_objects=None):
        self._smesh_component = object()
        self._mesh_objects = list(mesh_objects)
        self._recursive_objects = list(
            recursive_objects if recursive_objects is not None else mesh_objects
        )

    def FindComponent(self, name):
        return self._smesh_component if name == "SMESH" else None

    def NewChildIterator(self, component):
        return FakeIterator(self._mesh_objects, self._recursive_objects)

    def FindObjectID(self, object_id):
        return next(
            (o for o in self._recursive_objects if o.GetID() == object_id), None
        )


@unittest.skipUnless(
    QtWidgets is not None,
    "PyQt5 or PySide2 is required for OOFEMMainWidget tests: {}".format(
        _QT_IMPORT_ERROR
    ),
)
class MainWidgetWorkflowTests(unittest.TestCase):
    def setUp(self):
        # Guard against any accidental modal dialog blocking the test process.
        patches = [
            unittest.mock.patch.object(QtWidgets.QMessageBox, "information", lambda *a, **k: None),
            unittest.mock.patch.object(QtWidgets.QMessageBox, "warning", lambda *a, **k: None),
            unittest.mock.patch.object(QtWidgets.QMessageBox, "critical", lambda *a, **k: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        self.widget = OOFEMMainWidget()
        self.addCleanup(self.widget.deleteLater)
        self.mesh = FakeMesh()
        self.study_object = FakeStudyObject("0:1:2", "Panel", self.mesh)
        self.study = FakeStudy([self.study_object])
        self.widget.populateAll(study=self.study)

    def test_populate_all_discovers_mesh_and_element_mapping_defaults(self):
        self.assertEqual(self.widget.meshCombo.count(), 1)
        self.assertEqual(self.widget.meshCombo.currentText(), "Panel")
        self.assertEqual(
            self.widget.state["element_mapping"].get("Quadrangle"), "PlaneStress2d"
        )

    def test_populate_all_loads_dormant_smesh_component_of_an_opened_study(self):
        """A just-opened HDF study lists its meshes but has no live objects yet.

        SALOME only materialises the CORBA objects behind the SMESH branch once
        the component is loaded, so GetObject() returns None for every mesh
        until then.  Without the retry the combo stays empty and the mesh
        appears only when the user clicks Mesh or Geometry, which activates
        SMESH by accident.
        """
        mesh = FakeMesh()
        dormant = FakeStudyObject("0:1:2", "Samal", None)
        study = FakeStudy([dormant])

        def wake_up(_study):
            dormant._obj = mesh
            return study._smesh_component

        with unittest.mock.patch(
            "OOFEMSalomePlugin.OOFEMSalome.load_smesh_component", wake_up
        ) as loader:
            self.widget.populateAll(study=study)

        self.assertEqual(self.widget.meshCombo.count(), 1)
        self.assertEqual(self.widget.meshCombo.currentText(), "Samal")
        self.assertEqual(self.widget.meshCombo.currentData(), "0:1:2")

    def test_populate_all_does_not_reload_smesh_when_meshes_are_already_live(self):
        calls = []

        def loader(study):
            calls.append(study)
            return None

        with unittest.mock.patch(
            "OOFEMSalomePlugin.OOFEMSalome.load_smesh_component", loader
        ):
            self.widget.populateAll(study=self.study)

        self.assertEqual(calls, [])
        self.assertEqual(self.widget.meshCombo.count(), 1)

    def test_populate_all_discovers_mesh_inside_study_folder(self):
        folder = FakeStudyObject("0:1:10", "Meshes", None)
        nested_mesh = FakeMesh()
        nested = FakeStudyObject("0:1:10:1", "Nested panel", nested_mesh)
        study = FakeStudy([folder], recursive_objects=[folder, nested])

        self.widget.populateAll(study=study)

        self.assertEqual(self.widget.meshCombo.count(), 1)
        self.assertEqual(self.widget.meshCombo.currentText(), "Nested panel")
        self.assertEqual(self.widget.meshCombo.currentData(), "0:1:10:1")

    def _add_sheet_material(self):
        self.widget.state["materials"].append(
            {
                "id": "mat-1",
                "name": "sheet",
                "oofem_type": "ElasticIsotropic2d",
                "assigned_group": "MAT_FACES",
                "params": {"E": 1000.0, "nu": 0.25, "t": 1.0},
            }
        )
        self.widget.populateMaterials()

    def test_add_and_remove_material_round_trips_through_state_and_table(self):
        self._add_sheet_material()
        self.assertEqual(self.widget.matTable.rowCount(), 1)

        self.widget.matTable.selectRow(0)
        self.widget.removeMaterial()
        self.assertEqual(self.widget.matTable.rowCount(), 0)
        self.assertEqual(self.widget.state["materials"], [])

    def test_material_property_edit_coerces_float_and_clears_optional(self):
        self._add_sheet_material()
        self.widget.matTable.selectRow(0)

        # Column order follows OOFEMMaterials.json: E, nu, t, d (optional), alpha (optional).
        e_item = self.widget.matPropsTable.item(0, 1)
        e_item.setText("2100.5")
        self.assertEqual(
            self.widget.state["materials"][0]["params"]["E"], 2100.5
        )

        density_item = self.widget.matPropsTable.item(3, 1)
        density_item.setText("7850")
        self.assertEqual(
            self.widget.state["materials"][0]["params"]["d"], 7850.0
        )
        density_item.setText("")
        self.assertNotIn("d", self.widget.state["materials"][0]["params"])

    def _add_bc(self, oofem_type, group, dof, val):
        self.widget.state["bcs"].append(
            {
                "id": "bc-{}".format(len(self.widget.state["bcs"]) + 1),
                "name": "bc",
                "oofem_type": oofem_type,
                "assigned_group": group,
                "params": {"dof": dof, "val": val},
            }
        )
        self.widget.populateBCs()

    def test_bc_property_edit_rejects_invalid_int_without_saving(self):
        self._add_bc("Displacement", "BC_FIXED", 1, 0.0)
        self.widget.bcTable.selectRow(0)

        dof_item = self.widget.bcPropsTable.item(0, 1)
        dof_item.setText("not-a-number")
        self.assertEqual(self.widget.state["bcs"][0]["params"]["dof"], 1)
        # The rejected text must not linger in the cell as if it had been
        # applied: it should be reverted to the last known-good value.
        self.assertEqual(dof_item.text(), "1")

        dof_item.setText("2")
        self.assertEqual(self.widget.state["bcs"][0]["params"]["dof"], 2)

    def test_material_property_edit_rejects_non_finite_value_and_reverts_cell(self):
        self._add_sheet_material()
        self.widget.matTable.selectRow(0)

        # Column order follows OOFEMMaterials.json: E, nu, t, d (optional), alpha (optional).
        e_item = self.widget.matPropsTable.item(0, 1)
        for bad_value in ("nan", "inf", "-inf", "not-a-number"):
            e_item.setText(bad_value)
            self.assertEqual(
                self.widget.state["materials"][0]["params"]["E"], 1000.0
            )
            self.assertEqual(e_item.text(), "1000.0")

        e_item.setText("2100.5")
        self.assertEqual(
            self.widget.state["materials"][0]["params"]["E"], 2100.5
        )

    def test_edit_cross_section_group_resyncs_material_and_removal_clears_it(self):
        self._add_sheet_material()
        material = self.widget.state["materials"][0]

        with unittest.mock.patch.object(
            OOFEMCrossSectionDialog,
            "run",
            return_value={
                "id": "cs-1",
                "name": "sheet cross section",
                "oofem_type": "simplecs",
                "material_id": material["id"],
                "assigned_group": "MAT_FACES",
                "element_options": {"nlgeo": "inherit"},
                "params": {"thick": 1.0},
            },
        ):
            self.widget.addCrossSection()

        self.assertEqual(material.get("assigned_group"), "MAT_FACES")

        # Retarget the cross section to a different mesh group: the
        # material's own (legacy-fallback) assigned_group must follow it,
        # or the Materials tab -- and later a cross-section deletion -- go
        # stale relative to what is actually being exported.
        self.widget.crossSectionTable.selectRow(0)
        with unittest.mock.patch.object(
            OOFEMCrossSectionDialog,
            "run",
            return_value={
                "id": "cs-1",
                "name": "sheet cross section",
                "oofem_type": "simplecs",
                "material_id": material["id"],
                "assigned_group": "BC_FIXED",
                "element_options": {"nlgeo": "inherit"},
                "params": {"thick": 1.0},
            },
        ):
            self.widget.editCrossSection()

        self.assertEqual(material.get("assigned_group"), "BC_FIXED")
        self.assertEqual(self.widget.matTable.item(0, 2).text(), "BC_FIXED")

        # Removing the only cross section referencing this material must
        # clear its now-unowned assigned_group rather than leaving a value
        # the user moved away from to be silently reused by the exporter's
        # legacy per-material fallback once no cross section is left.
        self.widget.crossSectionTable.selectRow(0)
        self.widget.removeCrossSection()
        self.assertIsNone(material.get("assigned_group"))

    def test_validate_model_reports_domain_and_counts_for_configured_mesh(self):
        self._add_sheet_material()
        self._add_bc("Displacement", "BC_FIXED", 1, 0.0)
        self._add_bc("SurfaceLoad", "LOAD_EDGE", 1, 1.0)

        summary = self.widget.validateModel()

        self.assertIsNotNone(summary)
        self.assertEqual(summary["domain"], "2dplanestress")
        self.assertEqual(summary["nodes"], 4)
        self.assertEqual(summary["elements"], 1)
        self.assertEqual(summary["materials"], 1)
        self.assertEqual(summary["boundary_conditions"], 2)

    def test_validate_model_surfaces_errors_without_raising(self):
        # No materials assigned: validation must fail gracefully, not crash.
        summary = self.widget.validateModel()
        self.assertIsNone(summary)
        self.assertIn("Validation failed", self.widget.exportSummaryLabel.text())

    def test_confirmed_edits_emit_project_changed_but_refresh_does_not(self):
        self._add_sheet_material()
        events = []
        self.widget.projectChanged.connect(events.append)

        self.widget.populateAll(study=self.study, state=self.widget.state)
        self.assertEqual(events, [])
        self.assertFalse(hasattr(self.widget, "saveBtn"))
        self.assertIn("File > Save", self.widget.persistenceLabel.text())

        self.widget.matTable.selectRow(0)
        value_item = self.widget.matPropsTable.item(0, 1)
        value_item.setText("invalid")
        self.assertEqual(events, [])
        value_item.setText("2200")
        self.assertEqual(events, [self.widget])

        events.clear()
        mapping_item = self.widget.elemTable.item(0, 1)
        mapping_item.setText("CustomElement")
        self.assertEqual(events, [self.widget])
        self.assertIn("CustomElement", self.widget.state["element_mapping"].values())

        events.clear()
        self.widget.state["last_run_id"] = "run-1"
        self.widget._clearRunSelection()
        self.assertEqual(events, [self.widget])
        events.clear()
        self.widget._clearRunSelection()
        self.assertEqual(events, [])

    def test_cancelled_dialog_does_not_emit_project_changed(self):
        events = []
        self.widget.projectChanged.connect(events.append)
        with unittest.mock.patch(
            "OOFEMSalomePlugin.OOFEMMainWidget.OOFEMMaterialDialog.run",
            return_value=None,
        ):
            self.widget.addMaterial()
        self.assertEqual(events, [])

    def test_save_state_commits_state_into_module_singleton(self):
        self._add_sheet_material()

        # OOFEMSalomePlugin.OOFEMModule is imported lazily throughout the
        # plugin (test_plugin_entry.py asserts registration never imports it
        # eagerly), so mirror that here and restore sys.modules afterward
        # instead of importing it at this file's top level.
        previous_entry = sys.modules.get("OOFEMSalomePlugin.OOFEMModule")
        import OOFEMSalomePlugin.OOFEMModule as module_mod

        previous_instance = module_mod._oofem_module_instance
        module_mod._oofem_module_instance = None
        try:
            self.widget.saveState()
            module = module_mod.getModule()
            self.assertIs(module.study_state, self.widget.state)
            self.assertEqual(module.study_state["materials"][0]["name"], "sheet")
        finally:
            module_mod._oofem_module_instance = previous_instance
            if previous_entry is None:
                sys.modules.pop("OOFEMSalomePlugin.OOFEMModule", None)
            else:
                sys.modules["OOFEMSalomePlugin.OOFEMModule"] = previous_entry


if __name__ == "__main__":
    unittest.main()
