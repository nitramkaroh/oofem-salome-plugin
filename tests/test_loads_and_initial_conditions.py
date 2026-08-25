"""Body-load, structural-temperature, and initial-condition regressions."""

import copy
import json
import pathlib
import re
import subprocess
import tempfile
import unittest


from tests.test_exporter import (
    OOFEM_BINARY,
    OOFEMExporter,
    OOFEMValidationError,
    boundary_templates,
    truss_model,
)
from OOFEMSalomePlugin.OOFEMProject import (
    migrate_project_state,
    new_project_state,
)


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_record(oofem_type, components, dofs=None, group="bars"):
    parameters = {"components": components}
    if dofs is not None:
        parameters["dofs"] = dofs
    return {
        "id": "load-{}".format(oofem_type.lower()),
        "name": "test {}".format(oofem_type),
        "oofem_type": oofem_type,
        "assigned_group": group,
        "params": parameters,
    }


def _initial_condition(
    condition_id="ic-1",
    group="loaded",
    dofs=None,
    conditions=None,
):
    return {
        "id": condition_id,
        "name": condition_id,
        "oofem_type": "InitialCondition",
        "assigned_group": group,
        "params": {
            "dofs": [1] if dofs is None else dofs,
            "conditions": {"u": 0.0} if conditions is None else conditions,
        },
    }


def _truss_exporter(
    loads=None, initial_conditions=None, material_updates=None, analysis=None
):
    mesh, mapping, materials, boundary_conditions = truss_model()
    materials = copy.deepcopy(materials)
    boundary_conditions = copy.deepcopy(boundary_conditions[:-1])
    boundary_conditions.extend(copy.deepcopy(loads or []))
    materials[0]["params"].update(material_updates or {})
    return OOFEMExporter(
        mesh,
        mapping,
        materials,
        boundary_conditions,
        boundary_templates(),
        solver_settings={"vtk": False},
        initial_conditions=copy.deepcopy(initial_conditions or []),
        analysis=copy.deepcopy(analysis) if analysis else None,
    )


class ProjectAndCatalogTests(unittest.TestCase):
    def test_catalog_exposes_verified_element_loads_and_initial_condition(self):
        catalog_path = (
            REPOSITORY_ROOT
            / "src"
            / "OOFEMSalomePlugin"
            / "OOFEMBCs.json"
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        boundary_conditions = {
            item["oofem_name"]: item
            for item in catalog["boundary_conditions"]
        }

        self.assertEqual(boundary_conditions["DeadWeight"]["apply_to"], "elements")
        self.assertEqual(
            boundary_conditions["StructuralTemperatureLoad"]["apply_to"],
            "elements",
        )
        self.assertEqual(
            catalog["initial_conditions"][0]["oofem_name"],
            "InitialCondition",
        )
        self.assertEqual(
            catalog["initial_conditions"][0]["params"][1]["default"],
            {"u": 0.0},
        )

    def test_schema_migrates_constant_initial_condition_without_mutation(self):
        legacy = {
            "project_id": "study-a",
            "initial_conditions": [
                {
                    "name": "initial velocity",
                    "assigned_group": "LOADED",
                    "params": {
                        "dof": 1,
                        "mode": "v",
                        "value": 2.5,
                        "extension": {"keep": True},
                    },
                }
            ],
        }

        migrated = migrate_project_state(legacy)
        migrated_again = migrate_project_state(migrated)
        initial_condition = migrated["initial_conditions"][0]

        self.assertEqual(migrated["project_id"], "study-a")
        self.assertEqual(initial_condition["id"], "ic-1")
        self.assertEqual(initial_condition["oofem_type"], "InitialCondition")
        self.assertEqual(initial_condition["params"]["dofs"], [1])
        self.assertEqual(initial_condition["params"]["conditions"], {"v": 2.5})
        self.assertEqual(
            initial_condition["params"]["extension"], {"keep": True}
        )
        self.assertEqual(migrated_again, migrated)
        self.assertEqual(
            legacy["initial_conditions"][0]["params"]["dof"], 1
        )
        self.assertEqual(new_project_state()["project_id"], "")
        self.assertEqual(new_project_state()["initial_conditions"], [])

    def test_migration_repairs_duplicate_initial_condition_ids_idempotently(self):
        state = {
            "initial_conditions": [
                {"id": "ic-1", "params": {}},
                {"params": {}},
                {"id": "ic-1", "params": {}},
                {"id": "ic-2", "params": {}},
            ]
        }

        migrated = migrate_project_state(state)

        self.assertEqual(
            [item["id"] for item in migrated["initial_conditions"]],
            ["ic-1", "ic-3", "ic-4", "ic-2"],
        )
        self.assertEqual(migrate_project_state(migrated), migrated)
        self.assertNotIn("id", state["initial_conditions"][1])
        self.assertEqual(state["initial_conditions"][2]["id"], "ic-1")


class LoadAndInitialConditionExporterTests(unittest.TestCase):
    def test_renders_body_temperature_and_initial_condition_records(self):
        exporter = _truss_exporter(
            loads=[
                _load_record("DeadWeight", [10.0], dofs=[1]),
                _load_record(
                    "StructuralTemperatureLoad", [30.0]
                ),
            ],
            initial_conditions=[
                _initial_condition(conditions={"u": 0.0, "v": 0.0})
            ],
            material_updates={"d": 4.0, "alpha": 1.2e-5},
        )

        summary = exporter.validate()
        self.assertEqual(summary["boundary_conditions"], 7)
        self.assertEqual(summary["initial_conditions"], 1)

        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / "loads.in"
            exporter.export(str(input_path))
            generated = input_path.read_text(encoding="utf-8")

        self.assertRegex(generated, r"\bnbc 7 nic 1 nltf 1 nset 4\b")
        self.assertIn(
            "DeadWeight 6 loadTimeFunction 1 components 3 10 0 0 set 1",
            generated,
        )
        self.assertIn(
            "StructTemperatureLoad 7 loadTimeFunction 1 "
            "components 2 30 0 set 1",
            generated,
        )
        self.assertIn(
            "InitialCondition 1 conditions 2 u 0 v 0 "
            "dofs 1 1 set 4",
            generated,
        )

    def test_load_type_lookup_is_case_insensitive_and_alias_aware(self):
        exporter = _truss_exporter(
            loads=[
                _load_record("deadweight", [2.0], dofs=[1]),
                _load_record("StructTemperatureLoad", [3.0]),
            ],
            material_updates={"d": 1.0, "alpha": 1.0e-5},
        )

        exporter.validate()
        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / "aliases.in"
            exporter.export(str(input_path))
            generated = input_path.read_text(encoding="utf-8")

        self.assertIn("DeadWeight 6 loadTimeFunction 1", generated)
        self.assertIn("StructTemperatureLoad 7 loadTimeFunction 1", generated)

    def test_load_and_initial_condition_dofs_are_strict_integers(self):
        for dofs in ([True], [1.5]):
            with self.subTest(record="load", dofs=dofs):
                exporter = _truss_exporter(
                    loads=[_load_record("DeadWeight", [1.0], dofs=dofs)],
                    material_updates={"d": 1.0},
                )
                with self.assertRaisesRegex(
                    OOFEMValidationError, "DOFs must be integers"
                ):
                    exporter.validate()

            with self.subTest(record="initial condition", dofs=dofs):
                exporter = _truss_exporter(
                    initial_conditions=[_initial_condition(dofs=dofs)]
                )
                with self.assertRaisesRegex(
                    OOFEMValidationError, "DOFs must be integers"
                ):
                    exporter.validate()

    def test_new_numeric_values_must_be_finite(self):
        invalid_cases = [
            (
                _truss_exporter(
                    loads=[
                        _load_record(
                            "DeadWeight", [float("nan")], dofs=[1]
                        )
                    ],
                    material_updates={"d": 1.0},
                ),
                "values must be finite numbers",
            ),
            (
                _truss_exporter(
                    loads=[
                        _load_record(
                            "StructuralTemperatureLoad", [float("inf")]
                        )
                    ],
                    material_updates={"alpha": 1.0e-5},
                ),
                "temperature components must be finite numbers",
            ),
            (
                _truss_exporter(
                    initial_conditions=[
                        _initial_condition(conditions={"u": float("-inf")})
                    ]
                ),
                "values must be finite numbers",
            ),
        ]

        for exporter, expected_message in invalid_cases:
            with self.subTest(expected_message=expected_message):
                with self.assertRaisesRegex(
                    OOFEMValidationError, expected_message
                ):
                    exporter.validate()

    def test_element_loads_require_material_prerequisites(self):
        invalid_cases = [
            ("DeadWeight", {}, "density parameter 'd' must be a positive"),
            ("DeadWeight", {"d": 0.0}, "density parameter 'd' must be a positive"),
            ("DeadWeight", {"d": -1.0}, "density parameter 'd' must be a positive"),
            (
                "DeadWeight",
                {"d": float("inf")},
                "density parameter 'd' must be a positive",
            ),
            (
                "StructuralTemperatureLoad",
                {},
                "thermal expansion parameter 'alpha' must be a non-zero",
            ),
            (
                "StructuralTemperatureLoad",
                {"alpha": 0.0},
                "thermal expansion parameter 'alpha' must be a non-zero",
            ),
            (
                "StructuralTemperatureLoad",
                {"alpha": float("nan")},
                "thermal expansion parameter 'alpha' must be a non-zero",
            ),
        ]

        for load_type, material_updates, expected_message in invalid_cases:
            with self.subTest(load_type=load_type, updates=material_updates):
                exporter = _truss_exporter(
                    loads=[_load_record(load_type, [1.0], dofs=[1])],
                    material_updates=material_updates,
                )
                with self.assertRaisesRegex(
                    OOFEMValidationError, expected_message
                ):
                    exporter.validate()

        exporter = _truss_exporter(
            loads=[
                _load_record("StructuralTemperatureLoad", [1.0])
            ],
            material_updates={"alpha": -1.0e-5},
        )
        exporter.validate()

    def test_nonzero_initial_condition_is_rejected_for_static_analysis(self):
        exporter = _truss_exporter(
            initial_conditions=[
                _initial_condition(conditions={"u": 0.125})
            ]
        )
        with self.assertRaisesRegex(
            OOFEMValidationError, "non-zero values.*no transient dynamics"
        ):
            exporter.validate()

        zero_exporter = _truss_exporter(
            initial_conditions=[
                _initial_condition(conditions={"u": 0.0, "v": 0.0})
            ]
        )
        self.assertEqual(zero_exporter.validate()["initial_conditions"], 1)

        # NonLinearStatic is incremental but still has no transient dynamics,
        # so OOFEM would ignore non-zero initial conditions there as well.
        nonlinear_exporter = _truss_exporter(
            initial_conditions=[
                _initial_condition(conditions={"v": 0.5})
            ],
            analysis={
                "id": "analysis-nonlinear",
                "oofem_type": "NonLinearStatic",
                "params": {"nsteps": 2},
            },
        )
        with self.assertRaisesRegex(
            OOFEMValidationError, "non-zero values.*no transient dynamics"
        ):
            nonlinear_exporter.validate()

    def test_load_parameter_and_target_validation(self):
        invalid_cases = [
            (
                _load_record("DeadWeight", [1.0], dofs=[4]),
                "uses DOF 4",
            ),
            (
                _load_record("DeadWeight", [1.0], dofs=[1], group="fixed"),
                "requires an element group",
            ),
            (
                _load_record(
                    "StructuralTemperatureLoad", [1.0, 2.0, 3.0]
                ),
                "needs one temperature increment component",
            ),
            (
                _load_record(
                    "StructuralTemperatureLoad", [30.0, 1.0]
                ),
                "non-zero structural temperature-gradient component",
            ),
        ]

        for load, expected_message in invalid_cases:
            with self.subTest(expected_message=expected_message):
                exporter = _truss_exporter(loads=[load])
                with self.assertRaisesRegex(
                    OOFEMValidationError, expected_message
                ):
                    exporter.validate()

    def test_initial_condition_validation_rejects_invalid_and_overlapping_dofs(self):
        invalid_cases = [
            (
                [_initial_condition(conditions={"increment": 1.0})],
                "unsupported value mode 'increment'",
            ),
            (
                [_initial_condition(group="bars")],
                "requires a node group",
            ),
            (
                [_initial_condition(group="fixed", dofs=[1])],
                "overlaps prescribed displacement",
            ),
            (
                [
                    _initial_condition(condition_id="ic-first"),
                    _initial_condition(condition_id="ic-second"),
                ],
                "overlaps 'ic-first'",
            ),
        ]

        for initial_conditions, expected_message in invalid_cases:
            with self.subTest(expected_message=expected_message):
                exporter = _truss_exporter(
                    initial_conditions=initial_conditions
                )
                with self.assertRaisesRegex(
                    OOFEMValidationError, expected_message
                ):
                    exporter.validate()


@unittest.skipUnless(
    OOFEM_BINARY,
    "set OOFEM_BIN to run generated load inputs with a real OOFEM solver",
)
class LoadAndInitialConditionSolverTests(unittest.TestCase):
    @staticmethod
    def _displacement(output, node_number, dof=1):
        node = re.search(
            r"Node\s+{}\s+\([^)]*\):(.*?)(?:Node|Element output:)".format(
                node_number
            ),
            output,
            re.S,
        )
        if node is None:
            raise AssertionError("No output for node {}".format(node_number))
        displacement = re.search(
            r"dof\s+{}\s+d\s+([+\-0-9.eE]+)".format(dof), node.group(1)
        )
        if displacement is None:
            raise AssertionError(
                "No displacement for node {} DOF {}".format(node_number, dof)
            )
        return float(displacement.group(1))

    def _solve(self, exporter):
        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / "verified-load.in"
            exporter.export(str(input_path))
            generated = input_path.read_text(encoding="utf-8")
            result = subprocess.run(
                [OOFEM_BINARY, "-f", str(input_path)],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("0 error(s)", result.stdout)
            output = input_path.with_suffix(".out").read_text(encoding="utf-8")
        return generated, output

    def test_deadweight_matches_uniform_axial_body_force_solution(self):
        exporter = _truss_exporter(
            loads=[_load_record("DeadWeight", [10.0], dofs=[1])],
            material_updates={"d": 4.0},
        )

        generated, output = self._solve(exporter)

        self.assertIn("DeadWeight 6 loadTimeFunction 1", generated)
        # u(L) = rho * acceleration * L^2 / (2 E)
        self.assertAlmostEqual(
            self._displacement(output, 2), 0.1, delta=1.0e-12
        )

    def test_temperature_load_matches_free_expansion_solution(self):
        exporter = _truss_exporter(
            loads=[
                _load_record(
                    "StructuralTemperatureLoad", [30.0]
                )
            ],
            material_updates={"alpha": 1.2e-5},
        )

        generated, output = self._solve(exporter)

        self.assertIn(
            "StructTemperatureLoad 6 loadTimeFunction 1 "
            "components 2 30 0",
            generated,
        )
        # u(L) = alpha * delta_T * L
        self.assertAlmostEqual(
            self._displacement(output, 2), 3.6e-4, delta=1.0e-12
        )

    def test_initial_condition_record_is_accepted_by_static_solver(self):
        mesh, mapping, materials, boundary_conditions = truss_model()
        exporter = OOFEMExporter(
            mesh,
            mapping,
            copy.deepcopy(materials),
            copy.deepcopy(boundary_conditions),
            boundary_templates(),
            solver_settings={"vtk": False},
            initial_conditions=[_initial_condition()],
        )

        generated, output = self._solve(exporter)

        self.assertIn("InitialCondition 1 conditions 1 u 0", generated)
        self.assertAlmostEqual(
            self._displacement(output, 2), 0.025, delta=1.0e-12
        )


if __name__ == "__main__":
    unittest.main()
