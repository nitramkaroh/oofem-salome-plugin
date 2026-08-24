"""Contract tests for the explicit version-2 OOFEM project records."""

import copy
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from tests.test_exporter import (  # noqa: E402
    OOFEM_BINARY,
    OOFEMExporter,
    OOFEMValidationError,
    boundary_templates,
    ogden_quad_model,
    plane_stress_model,
    truss_model,
)


def _explicit_plane_stress_model(thickness=0.75):
    mesh, mapping, materials, bcs = plane_stress_model()
    materials = copy.deepcopy(materials)
    bcs = copy.deepcopy(bcs)
    material = materials[0]
    material.pop("assigned_group", None)
    material["params"].pop("t", None)
    cross_sections = [
        {
            "id": "cs-sheet",
            "name": "sheet section",
            "oofem_type": "SimpleCS",
            "material_id": material["id"],
            "assigned_group": "sheet",
            "params": {"thick": thickness},
        }
    ]
    return mesh, mapping, materials, bcs, cross_sections


def _explicit_truss_model():
    mesh, mapping, materials, unused_bcs = truss_model()
    materials = copy.deepcopy(materials)
    material = materials[0]
    material.pop("assigned_group", None)
    material["params"].pop("A", None)
    cross_sections = [
        {
            "id": "cs-bars",
            "name": "bar section",
            "oofem_type": "SimpleCS",
            "material_id": material["id"],
            "assigned_group": "bars",
            "params": {"area": 2.0},
        }
    ]
    bcs = [
        {
            "id": "fixed",
            "name": "fixed node",
            "oofem_type": "Displacement",
            "assigned_group": "fixed",
            "params": {"dofs": [1, 2, 3], "values": [0.0, 0.0, 0.0]},
        },
        {
            "id": "roller",
            "name": "roller node",
            "oofem_type": "Displacement",
            "assigned_group": "roller",
            "params": {"dofs": [2, 3], "values": [0.0, 0.0]},
        },
        {
            "id": "load",
            "name": "axial load",
            "oofem_type": "NodalLoad",
            "assigned_group": "loaded",
            "params": {"dofs": [1], "components": [10.0]},
        },
    ]
    return mesh, mapping, materials, bcs, cross_sections


def _array_boundary_conditions(boundary_conditions, time_function_id=None):
    result = copy.deepcopy(boundary_conditions)
    for boundary_condition in result:
        parameters = boundary_condition["params"]
        dof = parameters.pop("dof")
        value = parameters.pop("val")
        parameters["dofs"] = [dof]
        value_key = (
            "values"
            if boundary_condition["oofem_type"] == "Displacement"
            else "components"
        )
        parameters[value_key] = [value]
        if time_function_id is not None:
            boundary_condition["time_function_id"] = time_function_id
    return result


class ExporterV2Tests(unittest.TestCase):
    def _plane_exporter(self, **overrides):
        mesh, mapping, materials, bcs, cross_sections = (
            _explicit_plane_stress_model()
        )
        arguments = {
            "solver_settings": {"vtk": False},
            "cross_sections": cross_sections,
        }
        arguments.update(overrides)
        return OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            **arguments
        )

    def test_explicit_simplecs_is_independent_of_material_assignment(self):
        mesh, mapping, materials, bcs, cross_sections = (
            _explicit_plane_stress_model(thickness=0.75)
        )
        self.assertNotIn("assigned_group", materials[0])
        self.assertNotIn("t", materials[0]["params"])
        exporter = OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={"vtk": False},
            cross_sections=cross_sections,
        )

        summary = exporter.validate()
        self.assertEqual(summary["materials"], 1)
        self.assertEqual(summary["cross_sections"], 1)

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "explicit.in"
            exporter.export(str(path))
            model = path.read_text(encoding="utf-8")
        self.assertIn("SimpleCS 1 thick 0.75 material 1 set 1", model)

    def test_element_nlgeo_mode_overrides_or_inherits_solver_default(self):
        cases = (
            ("on", False, True),
            ("off", True, False),
            ("inherit", True, True),
            ("inherit", False, False),
        )
        for mode, solver_default, expected_enabled in cases:
            with self.subTest(mode=mode, solver_default=solver_default):
                mesh, mapping, materials, bcs, cross_sections = (
                    _explicit_plane_stress_model()
                )
                cross_sections[0]["element_options"] = {"nlgeo": mode}
                exporter = OOFEMExporter(
                    mesh,
                    mapping,
                    materials,
                    bcs,
                    boundary_templates(),
                    solver_settings={
                        "vtk": False,
                        "nlgeom": solver_default,
                    },
                    cross_sections=cross_sections,
                )
                with tempfile.TemporaryDirectory() as directory:
                    path = pathlib.Path(directory) / "nlgeo.in"
                    exporter.export(str(path))
                    element_record = next(
                        line
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if line.startswith("TrPlaneStress2d ")
                    )

                self.assertEqual(" nlgeo 1" in element_record, expected_enabled)

    def test_invalid_element_nlgeo_mode_is_rejected(self):
        mesh, mapping, materials, bcs, cross_sections = (
            _explicit_plane_stress_model()
        )
        cross_sections[0]["element_options"] = {"nlgeo": "sometimes"}
        exporter = OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={"vtk": False},
            cross_sections=cross_sections,
        )

        with self.assertRaisesRegex(
            OOFEMValidationError, r"invalid element nlgeo mode.*inherit, on, or off"
        ):
            exporter.validate()

    def test_analysis_param_does_not_override_inherited_solver_default(self):
        exporter = self._plane_exporter(
            solver_settings={"vtk": False, "nlgeom": False},
            analysis={
                "oofem_type": "StaticStructural",
                "params": {"nsteps": 1, "nlgeom": True},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "analysis_nlgeo.in"
            exporter.export(str(path))
            model = path.read_text(encoding="utf-8")

        self.assertNotIn(" nlgeo ", model)

    def test_hyperelastic_assignment_requires_effective_nlgeo_on(self):
        mesh, mapping, materials, bcs = ogden_quad_model()
        materials = copy.deepcopy(materials)
        materials[0].pop("assigned_group", None)
        cross_section = {
            "id": "cs-ogden",
            "name": "ogden section",
            "oofem_type": "SimpleCS",
            "material_id": "material-ogden",
            "assigned_group": "sheet",
            "element_options": {"nlgeo": "off"},
            "params": {"thick": 1.0},
        }
        exporter = OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={"vtk": False, "nlgeom": True},
            cross_sections=[cross_section],
        )

        with self.assertRaisesRegex(
            OOFEMValidationError, r"Hyperelastic material.*requires.*nlgeo"
        ):
            exporter.validate()

        enabled = copy.deepcopy(cross_section)
        enabled["element_options"]["nlgeo"] = "on"
        OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={"vtk": False, "nlgeom": False},
            cross_sections=[enabled],
        ).validate()

    def test_cross_section_references_and_assignments_are_validated(self):
        mesh, mapping, materials, bcs, cross_sections = (
            _explicit_plane_stress_model()
        )
        invalid_cases = [
            (
                [dict(cross_sections[0], material_id="missing-material")],
                "references missing material 'missing-material'",
            ),
            (
                [
                    {
                        key: value
                        for key, value in cross_sections[0].items()
                        if key != "assigned_group"
                    }
                ],
                "is not assigned to a mesh group",
            ),
            (
                cross_sections
                + [
                    dict(
                        cross_sections[0],
                        id="cs-sheet-duplicate",
                        name="duplicate sheet section",
                    )
                ],
                "has more than one cross-section assignment",
            ),
        ]

        for invalid_cross_sections, expected_message in invalid_cases:
            with self.subTest(expected_message=expected_message):
                exporter = OOFEMExporter(
                    mesh,
                    mapping,
                    copy.deepcopy(materials),
                    copy.deepcopy(bcs),
                    boundary_templates(),
                    solver_settings={"vtk": False},
                    cross_sections=copy.deepcopy(invalid_cross_sections),
                )
                with self.assertRaisesRegex(
                    OOFEMValidationError, expected_message
                ):
                    exporter.validate()

    def test_array_displacement_and_nodal_load_records(self):
        mesh, mapping, materials, bcs, cross_sections = _explicit_truss_model()
        exporter = OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={"vtk": False},
            cross_sections=cross_sections,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "arrays.in"
            exporter.export(str(path))
            model = path.read_text(encoding="utf-8")

        self.assertIn(
            "BoundaryCondition 1 loadTimeFunction 1 dofs 3 1 2 3 "
            "values 3 0 0 0 set 2",
            model,
        )
        self.assertIn(
            "BoundaryCondition 2 loadTimeFunction 1 dofs 2 2 3 "
            "values 2 0 0 set 3",
            model,
        )
        self.assertIn(
            "NodalLoad 3 loadTimeFunction 1 dofs 1 1 components 1 10 set 4",
            model,
        )

    def test_piecewise_time_function_reference_and_dynamic_nltf(self):
        mesh, mapping, materials, bcs, cross_sections = _explicit_truss_model()
        bcs[-1]["time_function_id"] = "ramp"
        time_functions = [
            {
                "id": "constant",
                "oofem_type": "ConstantFunction",
                "params": {"f(t)": 1.0},
            },
            {
                "id": "ramp",
                "oofem_type": "PiecewiseLinFunction",
                "params": {"t": [0.0, 5.0], "f(t)": [0.0, 1.0]},
            },
        ]
        exporter = OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={"vtk": False},
            cross_sections=cross_sections,
            time_functions=time_functions,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "piecewise.in"
            exporter.export(str(path))
            model = path.read_text(encoding="utf-8")

        self.assertRegex(model, r"nic 0 nltf 2 nset \d+")
        self.assertIn(
            "PiecewiseLinFunction 2 t 2 0 5 f(t) 2 0 1", model
        )
        self.assertIn("NodalLoad 3 loadTimeFunction 2", model)

    def test_linear_static_and_eigenvalue_dynamic_headers(self):
        cases = [
            (
                {
                    "id": "analysis-linear",
                    "oofem_type": "LinearStatic",
                    "params": {"nsteps": 3},
                },
                "LinearStatic nsteps 3",
            ),
            (
                {
                    "id": "analysis-eigen",
                    "oofem_type": "EigenValueDynamic",
                    "params": {"nroot": 7, "rtolv": 1.0e-7},
                },
                "EigenValueDynamic nroot 7 rtolv 1e-07",
            ),
        ]

        for analysis, expected_header in cases:
            with self.subTest(analysis=analysis["oofem_type"]):
                exporter = self._plane_exporter(analysis=analysis)
                with tempfile.TemporaryDirectory() as directory:
                    path = pathlib.Path(directory) / "analysis.in"
                    exporter.export(str(path))
                    header = path.read_text(encoding="utf-8").splitlines()[2]
                self.assertEqual(header, expected_header)

    def test_invalid_float_lists_and_time_references_fail_validation_before_write(self):
        invalid_time_functions = [
            {
                "id": "bad-ramp",
                "name": "bad ramp",
                "oofem_type": "PiecewiseLinFunction",
                "params": {"t": "0 not-a-number", "f(t)": [0.0, 1.0]},
            }
        ]
        exporter = self._plane_exporter(
            time_functions=invalid_time_functions
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "must-not-exist.in"
            with self.assertRaisesRegex(
                OOFEMValidationError, "Time function 'bad ramp' is invalid"
            ):
                exporter.validate()
            self.assertFalse(path.exists())

        mesh, mapping, materials, bcs, cross_sections = (
            _explicit_plane_stress_model()
        )
        bcs[0]["time_function_id"] = "missing-ltf"
        exporter = OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={"vtk": False},
            cross_sections=cross_sections,
            time_functions=[
                {
                    "id": "constant",
                    "oofem_type": "ConstantFunction",
                    "params": {"f(t)": 1.0},
                }
            ],
        )
        with self.assertRaisesRegex(
            OOFEMValidationError,
            "references missing time function 'missing-ltf'",
        ):
            exporter.validate()


@unittest.skipUnless(
    OOFEM_BINARY,
    "set OOFEM_BIN to run the version-2 ramped solve",
)
class ExporterV2SolverTests(unittest.TestCase):
    @staticmethod
    def _dof_displacement(output, time, node_number, dof):
        time_blocks = re.findall(
            r"Output for time\s+([+\-0-9.eE]+)(.*?)(?=Output for time|\Z)",
            output,
            re.S,
        )
        matching_block = next(
            (block for value, block in time_blocks if abs(float(value) - time) < 1.0e-9),
            None,
        )
        if matching_block is None:
            raise AssertionError("No OOFEM output block for time {}".format(time))
        node = re.search(
            r"Node\s+{}\s+\([^)]*\):(.*?)(?:Node|Element output:)".format(
                node_number
            ),
            matching_block,
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

    def test_ramped_plane_stress_reaches_one_fifth_then_full_displacement(self):
        mesh, mapping, materials, bcs, cross_sections = (
            _explicit_plane_stress_model(thickness=1.0)
        )
        bcs = _array_boundary_conditions(bcs, time_function_id="ramp")
        exporter = OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={"vtk": False},
            cross_sections=cross_sections,
            time_functions=[
                {
                    "id": "ramp",
                    "oofem_type": "PiecewiseLinFunction",
                    "params": {"t": [0.0, 5.0], "f(t)": [0.0, 1.0]},
                }
            ],
            analysis={
                "id": "analysis-ramp",
                "oofem_type": "StaticStructural",
                "params": {"nsteps": 5, "deltat": 1.0},
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / "ramped-plane.in"
            exporter.export(str(input_path))
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

        first_displacement = self._dof_displacement(output, 1.0, 3, 1)
        final_displacement = self._dof_displacement(output, 5.0, 3, 1)
        full_load_displacement = 3.53553391e-3
        self.assertAlmostEqual(
            first_displacement,
            full_load_displacement / 5.0,
            delta=1.0e-11,
        )
        self.assertAlmostEqual(
            final_displacement,
            full_load_displacement,
            delta=1.0e-11,
        )
        self.assertAlmostEqual(
            first_displacement / final_displacement, 0.2, delta=1.0e-9
        )


if __name__ == "__main__":
    unittest.main()
