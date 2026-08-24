import pathlib
import sys
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from OOFEMSalomePlugin.OOFEMProject import (  # noqa: E402
    PROJECT_SCHEMA_VERSION,
    migrate_project_state,
    new_project_state,
)


class OOFEMProjectTests(unittest.TestCase):
    def test_new_project_has_complete_fresh_schema(self):
        first = new_project_state()
        second = new_project_state()

        self.assertEqual(first["schema_version"], PROJECT_SCHEMA_VERSION)
        self.assertEqual(first["analysis"]["oofem_type"], "StaticStructural")
        self.assertEqual(first["analysis"]["params"], {"nsteps": 1})
        self.assertEqual(len(first["time_functions"]), 1)
        self.assertEqual(
            first["time_functions"][0],
            {
                "id": "ltf-1",
                "oofem_type": "ConstantFunction",
                "params": {"f(t)": 1.0},
            },
        )
        self.assertEqual(first["cross_sections"], [])
        self.assertEqual(first["materials"], [])
        self.assertEqual(first["bcs"], [])

        first["analysis"]["params"]["nsteps"] = 99
        first["time_functions"].append({"id": "other"})
        self.assertEqual(second["analysis"]["params"]["nsteps"], 1)
        self.assertEqual(len(second["time_functions"]), 1)

    def test_empty_and_none_migrate_to_new_project(self):
        empty = {}
        self.assertEqual(migrate_project_state(empty), new_project_state())
        self.assertEqual(empty, {})
        self.assertEqual(migrate_project_state(None), new_project_state())

    def test_legacy_state_is_migrated_to_explicit_entities(self):
        legacy = {
            "selected_mesh_id": "0:1:2",
            "element_mapping": {
                "Segment": "Truss3D",
                "Quadrangle": "PlaneStress2d",
            },
            "solver_preset": "large-strain-static-vtk",
            "oofem_executable": "/opt/oofem/bin/oofem",
            "last_input_file": "/work/panel.in",
            "materials": [
                {
                    "id": "mat-bar",
                    "name": "bar steel",
                    "oofem_type": "Truss",
                    "assigned_group": "BARS",
                    "params": {"E": 200.0, "A": 2.5},
                },
                {
                    "id": "mat-sheet",
                    "name": "sheet steel",
                    "oofem_type": "ElasticIsotropic2d",
                    "assigned_group": "SHEET",
                    "params": {"E": 1000.0, "nu": 0.25, "t": 0.2},
                },
                {
                    "id": "mat-solid",
                    "name": "solid steel",
                    "oofem_type": "ElasticIsotropic3d",
                    "assigned_group": "SOLID",
                    "params": {"E": 1000.0, "nu": 0.25},
                },
            ],
            "bcs": [
                {
                    "id": "bc-fixed",
                    "oofem_type": "Displacement",
                    "assigned_group": "FIXED",
                    "params": {"dof": 1, "val": 0.0, "note": "keep"},
                },
                {
                    "id": "bc-force",
                    "oofem_type": "NodalLoad",
                    "assigned_group": "LOAD",
                    "params": {"dof": 2, "val": -5.0},
                },
                {
                    "id": "bc-traction",
                    "oofem_type": "SurfaceLoad",
                    "assigned_group": "FACE",
                    "params": {"dof": 3, "val": 12.0},
                },
            ],
        }

        migrated = migrate_project_state(legacy)

        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["analysis"]["oofem_type"], "StaticStructural")
        self.assertEqual(
            migrated["time_functions"][0]["params"], {"f(t)": 1.0}
        )
        self.assertEqual(migrated["selected_mesh_id"], legacy["selected_mesh_id"])
        self.assertEqual(migrated["element_mapping"], legacy["element_mapping"])
        self.assertEqual(migrated["solver_preset"], legacy["solver_preset"])
        self.assertEqual(
            migrated["oofem_executable"], legacy["oofem_executable"]
        )
        self.assertEqual(migrated["last_input_file"], legacy["last_input_file"])

        fixed, force, traction = migrated["bcs"]
        self.assertEqual(
            fixed["params"], {"note": "keep", "dofs": [1], "values": [0.0]}
        )
        self.assertEqual(force["params"], {"dofs": [2], "components": [-5.0]})
        self.assertEqual(
            traction["params"], {"dofs": [3], "components": [12.0]}
        )
        self.assertTrue(
            all(bc["time_function_id"] == "ltf-1" for bc in migrated["bcs"])
        )

        self.assertEqual(len(migrated["cross_sections"]), 3)
        by_material = {
            cross_section["material_id"]: cross_section
            for cross_section in migrated["cross_sections"]
        }
        self.assertEqual(by_material["mat-bar"]["params"], {"area": 2.5})
        self.assertEqual(by_material["mat-sheet"]["params"], {"thick": 0.2})
        self.assertEqual(by_material["mat-solid"]["params"], {})
        self.assertEqual(by_material["mat-bar"]["assigned_group"], "BARS")
        self.assertEqual(by_material["mat-bar"]["oofem_type"], "SimpleCS")
        # The legacy fields remain available until GUI/exporter consumers move
        # entirely to explicit cross-section records.
        self.assertEqual(migrated["materials"][0]["params"]["A"], 2.5)
        self.assertEqual(migrated["materials"][1]["params"]["t"], 0.2)

    def test_migration_is_idempotent_and_does_not_duplicate_entities(self):
        legacy = {
            "materials": [
                {
                    "id": "mat-1",
                    "name": "bar",
                    "assigned_group": "BARS",
                    "params": {"A": 3.0},
                }
            ],
            "bcs": [
                {
                    "oofem_type": "NodalLoad",
                    "params": {"dof": 1, "val": 4.0},
                }
            ],
        }
        once = migrate_project_state(legacy)
        twice = migrate_project_state(once)

        self.assertEqual(twice, once)
        self.assertEqual(len(twice["time_functions"]), 1)
        self.assertEqual(len(twice["cross_sections"]), 1)

    def test_migration_is_a_deep_copy_for_legacy_and_v2_inputs(self):
        legacy = {
            "materials": [
                {
                    "id": "mat-1",
                    "assigned_group": "BARS",
                    "params": {"A": 2.0, "nested": {"labels": ["original"]}},
                }
            ],
            "bcs": [
                {
                    "oofem_type": "Displacement",
                    "params": {"dof": 1, "val": 0.0},
                }
            ],
        }
        migrated = migrate_project_state(legacy)
        migrated["materials"][0]["params"]["nested"]["labels"].append("changed")
        migrated["bcs"][0]["params"]["dofs"][0] = 3

        self.assertEqual(
            legacy["materials"][0]["params"]["nested"]["labels"], ["original"]
        )
        self.assertEqual(legacy["bcs"][0]["params"], {"dof": 1, "val": 0.0})

        canonical = new_project_state()
        canonical_copy = migrate_project_state(canonical)
        self.assertEqual(canonical_copy, canonical)
        self.assertIsNot(canonical_copy, canonical)
        self.assertIsNot(canonical_copy["analysis"], canonical["analysis"])
        self.assertIsNot(
            canonical_copy["time_functions"], canonical["time_functions"]
        )

    def test_unknown_extension_keys_are_preserved_at_every_level(self):
        state = {
            "vendor": {"project": [1, 2]},
            "analysis": {"custom_solver_option": {"tolerance": 1.0e-8}},
            "time_functions": [
                {
                    "id": "custom-ltf",
                    "oofem_type": "ConstantFunction",
                    "params": {"f(t)": 2.0, "vendor_parameter": "keep"},
                    "vendor_tf": True,
                }
            ],
            "materials": [
                {
                    "id": "mat-x",
                    "assigned_group": "DOMAIN",
                    "params": {"E": 7.0, "vendor_material_param": ["x"]},
                    "vendor_material": {"colour": "blue"},
                }
            ],
            "bcs": [
                {
                    "id": "bc-x",
                    "oofem_type": "Displacement",
                    "params": {
                        "dof": 1,
                        "val": 0.0,
                        "vendor_bc_param": {"flag": True},
                    },
                    "vendor_bc": ["keep"],
                }
            ],
            "cross_sections": [
                {
                    "id": "cs-custom",
                    "oofem_type": "SimpleCS",
                    "material_id": "mat-x",
                    "assigned_group": "DOMAIN",
                    "params": {},
                    "vendor_cs": 42,
                }
            ],
        }
        migrated = migrate_project_state(state)

        self.assertEqual(migrated["vendor"], {"project": [1, 2]})
        self.assertEqual(
            migrated["analysis"]["custom_solver_option"], {"tolerance": 1.0e-8}
        )
        self.assertEqual(migrated["time_functions"][0]["vendor_tf"], True)
        self.assertEqual(
            migrated["time_functions"][0]["params"]["vendor_parameter"], "keep"
        )
        self.assertEqual(
            migrated["materials"][0]["vendor_material"], {"colour": "blue"}
        )
        self.assertEqual(
            migrated["materials"][0]["params"]["vendor_material_param"], ["x"]
        )
        self.assertEqual(migrated["bcs"][0]["vendor_bc"], ["keep"])
        self.assertEqual(
            migrated["bcs"][0]["params"]["vendor_bc_param"], {"flag": True}
        )
        self.assertEqual(migrated["cross_sections"][0]["vendor_cs"], 42)
        self.assertEqual(len(migrated["cross_sections"]), 1)

    def test_existing_array_records_and_time_function_reference_are_kept(self):
        state = {
            "time_functions": [
                {
                    "id": "load-curve",
                    "oofem_type": "ConstantFunction",
                    "params": {"f(t)": 3.0},
                }
            ],
            "bcs": [
                {
                    "oofem_type": "NodalLoad",
                    "time_function_id": "load-curve",
                    "params": {"dofs": [1, 2], "components": [4.0, 5.0]},
                }
            ],
        }
        migrated = migrate_project_state(state)

        self.assertEqual(migrated["time_functions"], state["time_functions"])
        self.assertEqual(migrated["bcs"], state["bcs"])

    def test_non_dictionary_state_is_rejected(self):
        with self.assertRaises(TypeError):
            migrate_project_state([])


if __name__ == "__main__":
    unittest.main()
