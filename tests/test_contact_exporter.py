import os
import pathlib
import re
import subprocess
import tempfile
import unittest


from tests.test_exporter import (
    ENTITY_ITEMS,
    FakeGroup,
    FakeStructuralMesh,
    OOFEMExporter,
    OOFEMValidationError,
    boundary_templates,
    fake_smesh,
)


OOFEM_BINARY = os.environ.get("OOFEM_BIN")


def contact_2d_model():
    """Two linear plane-strain bodies with one contact edge per body."""
    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("bodies", fake_smesh.FACE, [100, 200]),
            FakeGroup("master-edge", fake_smesh.EDGE, [500]),
            FakeGroup("slave-edge", fake_smesh.EDGE, [600]),
        ],
        connectivity={
            100: [10, 20, 30, 40],
            200: [50, 60, 70, 80],
            # Opposite orientations give the two bodies outward-facing normals.
            500: [30, 40],
            600: [50, 60],
        },
        element_types={
            100: ENTITY_ITEMS[6],
            200: ENTITY_ITEMS[6],
            500: ENTITY_ITEMS[0],
            600: ENTITY_ITEMS[0],
        },
        coordinates={
            10: (-0.5, -1.0, 0.0),
            20: (0.5, -1.0, 0.0),
            30: (0.5, 0.0, 0.0),
            40: (-0.5, 0.0, 0.0),
            50: (-0.5, -0.01, 0.0),
            60: (0.5, -0.01, 0.0),
            70: (0.5, 0.99, 0.0),
            80: (-0.5, 0.99, 0.0),
        },
    )
    materials = [
        {
            "id": "material-1",
            "name": "contact bodies",
            "oofem_type": "ElasticIsotropic2d",
            "assigned_group": "bodies",
            "params": {"E": 1000.0, "nu": 0.25, "t": 1.0},
        }
    ]
    return mesh, {"Quadrangle": "Quad1PlaneStrain"}, materials, []


def contact_definition(**parameter_overrides):
    parameters = {
        "normal_penalty": 1.0e6,
        "tangential_penalty": 2.5e5,
        "friction": 0.0,
        "algorithm": 0,
        "two_pass": False,
        "reverse_master": False,
        "reverse_slave": False,
    }
    parameters.update(parameter_overrides)
    return {
        "id": "contact-1",
        "name": "body interface",
        "oofem_type": "StructuralPenaltyContactBC",
        "master_group": "master-edge",
        "slave_group": "slave-edge",
        "time_function_id": "ltf-1",
        "params": parameters,
    }


def make_exporter(contact=None):
    return OOFEMExporter(
        *contact_2d_model(),
        boundary_templates(),
        contacts=[contact or contact_definition()],
        solver_settings={
            "engng_model": "StaticStructural",
            "nsteps": 1,
            "vtk": False,
            "nlgeom": False,
        },
    )


def make_solvable_exporter():
    mesh, element_map, materials, _unused_bcs = contact_2d_model()
    materials[0]["oofem_type"] = "mooneyrivlincompressiblemat"
    materials[0]["params"] = {
        "d": 0.0,
        "c1": 1000.0,
        "c2": 0.0,
        "k": 10000.0,
        "t": 1.0,
    }
    materials[0].pop("assigned_group", None)
    mesh.groups.extend(
        (
            FakeGroup("lower-fixed", fake_smesh.NODE, [10, 20]),
            FakeGroup("upper-fixed", fake_smesh.NODE, [70, 80]),
        )
    )
    boundary_conditions = [
        {
            "id": "bc-lower",
            "name": "fix lower body",
            "oofem_type": "Displacement",
            "assigned_group": "lower-fixed",
            "params": {"dofs": [1, 2], "values": [0.0, 0.0]},
        },
        {
            "id": "bc-upper",
            "name": "fix upper body",
            "oofem_type": "Displacement",
            "assigned_group": "upper-fixed",
            "params": {"dofs": [1, 2], "values": [0.0, 0.0]},
        },
    ]
    return OOFEMExporter(
        mesh,
        element_map,
        materials,
        boundary_conditions,
        boundary_templates(),
        cross_sections=[
            {
                "id": "cs-contact-bodies",
                "name": "hyperelastic contact bodies",
                "oofem_type": "SimpleCS",
                "material_id": "material-1",
                "assigned_group": "bodies",
                "element_options": {"nlgeo": "on"},
                "params": {"thick": 1.0},
            }
        ],
        contacts=[
            contact_definition(
                normal_penalty=1.0e4,
                tangential_penalty=1.0e4,
            )
        ],
        solver_settings={
            "engng_model": "StaticStructural",
            "nsteps": 1,
            "vtk": False,
            "nlgeom": False,
            "rtolv": 1.0e-10,
            "maxiter": 80,
            "manrmsteps": 1,
            "initialguess": 1,
            "smtype": 0,
            "stiffmode": 0,
            "renumber": 0,
        },
    )


def make_small_strain_solvable_exporter():
    exporter = make_solvable_exporter()
    exporter.mat_map[0]["oofem_type"] = "ElasticIsotropic2d"
    exporter.mat_map[0]["params"] = {
        "E": 1000.0,
        "nu": 0.25,
        "t": 1.0,
    }
    exporter.cross_sections[0]["element_options"]["nlgeo"] = "off"
    exporter.solver_settings["nlgeom"] = False
    return exporter


def make_3d_exporter():
    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("solids", fake_smesh.VOLUME, [100, 200]),
            FakeGroup("master-face", fake_smesh.FACE, [500]),
            FakeGroup("slave-face", fake_smesh.FACE, [600]),
        ],
        connectivity={
            100: [10, 20, 30, 40],
            200: [50, 60, 70, 80],
            500: [10, 30, 20],
            600: [50, 60, 70],
        },
        element_types={
            100: ENTITY_ITEMS[7],
            200: ENTITY_ITEMS[7],
            500: ENTITY_ITEMS[2],
            600: ENTITY_ITEMS[2],
        },
        coordinates={
            10: (0.0, 0.0, 0.0),
            20: (1.0, 0.0, 0.0),
            30: (0.0, 1.0, 0.0),
            40: (0.0, 0.0, -1.0),
            50: (0.0, 0.0, 0.01),
            60: (1.0, 0.0, 0.01),
            70: (0.0, 1.0, 0.01),
            80: (0.0, 0.0, 1.01),
        },
    )
    materials = [
        {
            "id": "material-3d",
            "name": "contact solids",
            "oofem_type": "ElasticIsotropic3d",
            "assigned_group": "solids",
            "params": {"E": 1000.0, "nu": 0.25},
        }
    ]
    contact = contact_definition(algorithm=1, reverse_slave=True)
    contact["master_group"] = "master-face"
    contact["slave_group"] = "slave-face"
    return OOFEMExporter(
        mesh,
        {"Tetrahedron": "LTRSpace"},
        materials,
        [],
        boundary_templates(),
        contacts=[contact],
        solver_settings={"vtk": False, "nlgeom": False},
    )


def export_text(exporter):
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "contact.in"
        exporter.export(str(path))
        return path.read_text(encoding="utf-8")


def records_with_keyword(model, keyword):
    prefix = keyword.casefold() + " "
    return [
        line.strip()
        for line in model.splitlines()
        if line.strip().casefold().startswith(prefix)
    ]


def numeric_record_value(record, keyword):
    match = re.search(
        r"(?:^|\s){}\s+([^\s]+)".format(re.escape(keyword)), record
    )
    if match is None:
        raise AssertionError("{} is missing from {!r}".format(keyword, record))
    return float(match.group(1))


class ContactExporterTests(unittest.TestCase):
    def test_exports_current_frictionless_2d_contact(self):
        exporter = make_exporter()
        summary = exporter.validate()
        model = export_text(exporter)

        self.assertEqual(summary["contact_elements"], 2)
        self.assertEqual(summary["contacts"], 1)
        self.assertEqual(summary["contact_conditions"], 1)
        self.assertEqual(summary["contact_surfaces"], 2)
        self.assertRegex(
            model,
            r"(?m)^ndofman 8 nelem 4 ncrosssect 2 nmat 2 nbc 1 "
            r"nic 0 nltf 1 nset 3 ncontactsurf 2$",
        )
        self.assertIn(
            "StructuralContactElement_LineLin 3 nodes 2 3 4 "
            "crosssect 2 mat 2 NIP 2",
            model,
        )
        self.assertIn(
            "StructuralContactElement_LineLin 4 nodes 2 5 6 "
            "crosssect 2 mat 2 NIP 2",
            model,
        )
        self.assertIn("StructuralFEContactSurface 1 ce_set 1", model)
        self.assertIn("StructuralFEContactSurface 2 ce_set 2", model)
        self.assertRegex(model, r"(?mi)^DummyCS 2 mat 2\s*$")
        self.assertRegex(model, r"(?mi)^DummyMat 2\s*$")
        self.assertNotIn(" nlgeo ", model)

        # Current OOFEM contact initialization resolves a Set with the same id
        # as each surface, so this is a compatibility requirement, not merely
        # an incidental export ordering detail.
        self.assertRegex(model, r"(?mi)^Set 1 elements 1 3\s*$")
        self.assertRegex(model, r"(?mi)^Set 2 elements 1 4\s*$")

        contact_records = records_with_keyword(
            model, "structuralpenaltycontactbc"
        )
        self.assertEqual(len(contact_records), 1)
        record = contact_records[0]
        for expected in (
            "loadTimeFunction 1",
            "dofs 2 1 2",
            "friction 0",
            "mastersurface 1",
            "slavesurface 2",
            "nsd 2",
        ):
            self.assertIn(expected, record)
        self.assertEqual(numeric_record_value(record, "pn"), 1.0e6)
        self.assertEqual(numeric_record_value(record, "pt"), 2.5e5)

    def test_two_pass_writes_reverse_condition_without_duplicate_surfaces(self):
        model = export_text(make_exporter(contact_definition(two_pass=True)))

        self.assertRegex(
            model,
            r"(?m)^ndofman 8 nelem 4 ncrosssect 2 nmat 2 nbc 2 "
            r"nic 0 nltf 1 nset 3 ncontactsurf 2$",
        )
        self.assertEqual(
            len(records_with_keyword(model, "StructuralContactElement_LineLin")),
            2,
        )
        self.assertEqual(
            len(records_with_keyword(model, "StructuralFEContactSurface")), 2
        )
        records = records_with_keyword(model, "structuralpenaltycontactbc")
        self.assertEqual(len(records), 2)
        self.assertIn("mastersurface 1 slavesurface 2", records[0])
        self.assertIn("mastersurface 2 slavesurface 1", records[1])
        self.assertTrue(all("friction 0" in record for record in records))

    def test_rejects_missing_contact_boundary_group(self):
        contact = contact_definition()
        contact["master_group"] = "missing-master"
        with self.assertRaisesRegex(
            OOFEMValidationError,
            r"(?i)(missing[^\n]*master|master[^\n]*missing)",
        ):
            make_exporter(contact).validate()

    def test_rejects_same_master_and_slave_group(self):
        contact = contact_definition()
        contact["slave_group"] = contact["master_group"]
        with self.assertRaisesRegex(
            OOFEMValidationError, r"(?i)master[^\n]*slave[^\n]*differ"
        ):
            make_exporter(contact).validate()

    def test_rejects_differently_named_groups_with_the_same_facet(self):
        mesh, element_map, materials, boundary_conditions = contact_2d_model()
        mesh.groups.append(
            FakeGroup("duplicate-master", fake_smesh.EDGE, [500])
        )
        contact = contact_definition()
        contact["slave_group"] = "duplicate-master"
        exporter = OOFEMExporter(
            mesh,
            element_map,
            materials,
            boundary_conditions,
            boundary_templates(),
            contacts=[contact],
            solver_settings={"vtk": False, "nlgeom": True},
        )
        with self.assertRaisesRegex(
            OOFEMValidationError, r"(?i)same[^\n]*facet|self-contact"
        ):
            exporter.validate()

    def test_rejects_duplicate_or_reversed_physical_contact_pair(self):
        first = contact_definition(two_pass=True)
        second = contact_definition()
        second["id"] = "contact-2"
        second["name"] = "reversed duplicate"
        second["master_group"], second["slave_group"] = (
            second["slave_group"],
            second["master_group"],
        )
        exporter = make_exporter(first)
        exporter.contacts.append(second)

        with self.assertRaisesRegex(
            OOFEMValidationError,
            r"(?i)overlaps physical pair|duplicate.*penalty",
        ):
            exporter.validate()

    def test_contact_carriers_follow_parent_facet_orientation(self):
        mesh, element_map, materials, boundary_conditions = contact_2d_model()
        # Standalone SMESH carriers deliberately oppose their owning facets.
        mesh.connectivity[500] = [40, 30]
        mesh.connectivity[600] = [60, 50]
        exporter = OOFEMExporter(
            mesh,
            element_map,
            materials,
            boundary_conditions,
            boundary_templates(),
            contacts=[contact_definition()],
            solver_settings={"vtk": False, "nlgeom": True},
        )

        model = export_text(exporter)

        self.assertIn(
            "StructuralContactElement_LineLin 3 nodes 2 3 4", model
        )
        self.assertIn(
            "StructuralContactElement_LineLin 4 nodes 2 5 6", model
        )

    def test_current_contact_exports_nonzero_friction_without_profile_choice(self):
        model = export_text(
            make_exporter(contact_definition(friction=0.2))
        )

        self.assertIn("friction 0.2", model)

    def test_contact_does_not_require_element_nlgeo(self):
        exporter = make_exporter()
        exporter.analysis = {
            "oofem_type": "StaticStructural",
            "params": {"nsteps": 10, "nlgeom": False},
        }

        summary = exporter.validate()
        model = export_text(exporter)

        self.assertEqual(summary["contacts"], 1)
        self.assertFalse(exporter._nonlinear_geometry_enabled())
        self.assertNotIn(" nlgeo ", model)

    def test_contact_allows_explicit_nlgeo_off_under_global_on_default(self):
        mesh, element_map, materials, boundary_conditions = contact_2d_model()
        materials[0].pop("assigned_group", None)
        exporter = OOFEMExporter(
            mesh,
            element_map,
            materials,
            boundary_conditions,
            boundary_templates(),
            contacts=[contact_definition()],
            cross_sections=[
                {
                    "id": "cs-bodies",
                    "name": "small-strain bodies",
                    "oofem_type": "SimpleCS",
                    "material_id": "material-1",
                    "assigned_group": "bodies",
                    "element_options": {"nlgeo": "off"},
                    "params": {"thick": 1.0},
                }
            ],
            solver_settings={"vtk": False, "nlgeom": True},
        )

        summary = exporter.validate()
        model = export_text(exporter)

        self.assertEqual(summary["contacts"], 1)
        self.assertNotIn(" nlgeo ", model)

    def test_element_groups_can_mix_nlgeo_on_and_off(self):
        mesh, element_map, materials, boundary_conditions = contact_2d_model()
        mesh.groups[0] = FakeGroup("body-on", fake_smesh.FACE, [100])
        mesh.groups.insert(1, FakeGroup("body-off", fake_smesh.FACE, [200]))
        materials[0].pop("assigned_group", None)
        exporter = OOFEMExporter(
            mesh,
            element_map,
            materials,
            boundary_conditions,
            boundary_templates(),
            cross_sections=[
                {
                    "id": "cs-on",
                    "name": "large-strain body",
                    "oofem_type": "SimpleCS",
                    "material_id": "material-1",
                    "assigned_group": "body-on",
                    "element_options": {"nlgeo": "on"},
                    "params": {"thick": 1.0},
                },
                {
                    "id": "cs-off",
                    "name": "small-strain body",
                    "oofem_type": "SimpleCS",
                    "material_id": "material-1",
                    "assigned_group": "body-off",
                    "element_options": {"nlgeo": "off"},
                    "params": {"thick": 1.0},
                },
            ],
            solver_settings={"vtk": False, "nlgeom": False},
        )

        model = export_text(exporter)
        element_records = records_with_keyword(model, "Quad1PlaneStrain")

        self.assertEqual(len(element_records), 2)
        self.assertIn(" nlgeo 1", element_records[0])
        self.assertNotIn(" nlgeo ", element_records[1])

    def test_internal_contact_set_name_cannot_shadow_salome_group(self):
        mesh, element_map, materials, boundary_conditions = contact_2d_model()
        colliding_name = "__contact_surface__:master-edge:forward"
        mesh.groups[0].name = colliding_name
        materials[0]["assigned_group"] = colliding_name
        exporter = OOFEMExporter(
            mesh,
            element_map,
            materials,
            boundary_conditions,
            boundary_templates(),
            contacts=[contact_definition()],
            solver_settings={"vtk": False, "nlgeom": True},
        )

        model = export_text(exporter)

        self.assertRegex(model, r"(?mi)^Set 1 elements 1 3\s*$")
        self.assertRegex(model, r"(?mi)^Set 2 elements 1 4\s*$")
        self.assertRegex(model, r"(?mi)^Set 3 elements 2 1 2\s*$")

    def test_rejects_sweep_and_prune_in_2d(self):
        with self.assertRaisesRegex(
            OOFEMValidationError, r"(?i)sweep-and-prune[^\n]*only[^\n]*3D"
        ):
            make_exporter(contact_definition(algorithm=1)).validate()

    def test_exports_3d_triangular_surfaces_and_sap_search(self):
        model = export_text(make_3d_exporter())

        self.assertIn(
            "StructuralContactElement_TrLin 3 nodes 3 1 3 2 "
            "crosssect 2 mat 2 NIP 3",
            model,
        )
        self.assertIn(
            "StructuralContactElement_TrLin 4 nodes 3 5 6 7 "
            "crosssect 2 mat 2 NIP 3",
            model,
        )
        record = records_with_keyword(model, "structuralpenaltycontactbc")[0]
        self.assertIn("dofs 3 1 2 3", record)
        self.assertIn("nsd 3", record)
        self.assertIn("algo 1", record)


CONTACT_ONLY_SMOKE_INPUT = """contact_only_smoke.out
Analytical frictionless 2D contact smoke test
StaticStructural nsteps 1 renumber 0 nmodules 0 rtolv 1.e-12 stiffMode 0 manrmsteps 1 maxiter 20 initialguess 1 smtype 0
domain PlaneStrain
OutputManager tstep_all dofman_all element_all
ndofman 4 nelem 2 ncrosssect 1 nmat 1 nbc 3 nic 0 nltf 1 nset 4 ncontactsurf 2
node 1 coords 2 0.5 0.0
node 2 coords 2 -0.5 0.0
node 3 coords 2 -0.5 -0.01
node 4 coords 2 0.5 -0.01
StructuralContactElement_LineLin 1 nodes 2 1 2 crosssect 1 mat 1 NIP 2
StructuralContactElement_LineLin 2 nodes 2 3 4 crosssect 1 mat 1 NIP 2
StructuralFEContactSurface 1 ce_set 1
StructuralFEContactSurface 2 ce_set 2
DummyCS 1 mat 1
DummyMat 1
BoundaryCondition 1 loadTimeFunction 1 values 2 0 0 dofs 2 1 2 set 3
BoundaryCondition 2 loadTimeFunction 1 values 2 0 0 dofs 2 1 2 set 4
structuralpenaltycontactbc 3 loadTimeFunction 1 dofs 2 1 2 pn 1000 pt 1000 friction 0 mastersurface 1 slavesurface 2 nsd 2
ConstantFunction 1 f(t) 1
Set 1 elements 1 1
Set 2 elements 1 2
Set 3 nodes 2 1 2
Set 4 nodes 2 3 4
"""


CONTACT_3D_TRI_SMOKE_INPUT = """contact3d_tri_smoke.out
3D triangle contact smoke
StaticStructural nsteps 1 renumber 0 nmodules 0 rtolv 1e-12 stiffMode 0 manrmsteps 1 maxiter 20 initialguess 1 smtype 0
domain 3d
OutputManager tstep_all dofman_all element_all
ndofman 6 nelem 2 ncrosssect 1 nmat 1 nbc 3 nic 0 nltf 1 nset 4 ncontactsurf 2
node 1 coords 3 0 0 0
node 2 coords 3 1 0 0
node 3 coords 3 0 1 0
node 4 coords 3 0 0 -0.01
node 5 coords 3 0 1 -0.01
node 6 coords 3 1 0 -0.01
StructuralContactElement_TrLin 1 nodes 3 1 2 3 crosssect 1 mat 1 NIP 3
StructuralContactElement_TrLin 2 nodes 3 4 5 6 crosssect 1 mat 1 NIP 3
StructuralFEContactSurface 1 ce_set 1
StructuralFEContactSurface 2 ce_set 2
DummyCS 1 mat 1
DummyMat 1
BoundaryCondition 1 loadTimeFunction 1 values 3 0 0 0 dofs 3 1 2 3 set 3
BoundaryCondition 2 loadTimeFunction 1 values 3 0 0 0 dofs 3 1 2 3 set 4
structuralpenaltycontactbc 3 loadTimeFunction 1 dofs 3 1 2 3 pn 1000 pt 1000 friction 0 mastersurface 1 slavesurface 2 nsd 3
ConstantFunction 1 f(t) 1
Set 1 elements 1 1
Set 2 elements 1 2
Set 3 nodes 3 1 2 3
Set 4 nodes 3 4 5 6
"""


CONTACT_3D_QUAD_SMOKE_INPUT = """contact3d_quad_smoke.out
3D quadrilateral contact smoke
StaticStructural nsteps 1 renumber 0 nmodules 0 rtolv 1e-12 stiffMode 0 manrmsteps 1 maxiter 20 initialguess 1 smtype 0
domain 3d
OutputManager tstep_all dofman_all element_all
ndofman 8 nelem 2 ncrosssect 1 nmat 1 nbc 3 nic 0 nltf 1 nset 4 ncontactsurf 2
node 1 coords 3 0 0 0
node 2 coords 3 1 0 0
node 3 coords 3 1 1 0
node 4 coords 3 0 1 0
node 5 coords 3 0 0 -0.01
node 6 coords 3 0 1 -0.01
node 7 coords 3 1 1 -0.01
node 8 coords 3 1 0 -0.01
StructuralContactElement_QuadLin 1 nodes 4 1 2 3 4 crosssect 1 mat 1 NIP 4
StructuralContactElement_QuadLin 2 nodes 4 5 6 7 8 crosssect 1 mat 1 NIP 4
StructuralFEContactSurface 1 ce_set 1
StructuralFEContactSurface 2 ce_set 2
DummyCS 1 mat 1
DummyMat 1
BoundaryCondition 1 loadTimeFunction 1 values 3 0 0 0 dofs 3 1 2 3 set 3
BoundaryCondition 2 loadTimeFunction 1 values 3 0 0 0 dofs 3 1 2 3 set 4
structuralpenaltycontactbc 3 loadTimeFunction 1 dofs 3 1 2 3 pn 1000 pt 1000 friction 0 mastersurface 1 slavesurface 2 nsd 3
ConstantFunction 1 f(t) 1
Set 1 elements 1 1
Set 2 elements 1 2
Set 3 nodes 4 1 2 3 4
Set 4 nodes 4 5 6 7 8
"""


@unittest.skipUnless(
    OOFEM_BINARY,
    "set OOFEM_BIN to run the contact syntax smoke test with a real OOFEM solver",
)
class ContactSolverSmokeTests(unittest.TestCase):
    def _assert_embedded_input_solves(self, filename, model):
        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / filename
            input_path.write_text(model, encoding="utf-8")
            result = subprocess.run(
                [OOFEM_BINARY, "-f", str(input_path)],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            diagnostics = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, diagnostics)
            self.assertIn("0 error(s)", result.stdout)

    def test_minimal_frictionless_contact_input_solves(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / "contact_only_smoke.in"
            input_path.write_text(CONTACT_ONLY_SMOKE_INPUT, encoding="utf-8")
            result = subprocess.run(
                [OOFEM_BINARY, "-f", str(input_path)],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            diagnostics = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, diagnostics)
            self.assertIn("0 error(s)", result.stdout)
            output_path = pathlib.Path(directory) / "contact_only_smoke.out"
            self.assertTrue(output_path.is_file(), diagnostics)
            output = output_path.read_text(encoding="utf-8")
            reactions = [
                abs(float(value))
                for value in re.findall(
                    r"reaction\s+[^\n]*?([+\-]\d+(?:\.\d*)?[eE][+\-]?\d+)",
                    output,
                    re.I,
                )
            ]
            self.assertTrue(reactions, output)
            self.assertAlmostEqual(
                max(reactions), 5.0, places=8
            )

    def test_3d_triangular_contact_carrier_solves(self):
        self._assert_embedded_input_solves(
            "contact3d_tri_smoke.in", CONTACT_3D_TRI_SMOKE_INPUT
        )

    def test_3d_quadrilateral_contact_carrier_solves(self):
        self._assert_embedded_input_solves(
            "contact3d_quad_smoke.in", CONTACT_3D_QUAD_SMOKE_INPUT
        )

    def test_exported_continuum_contact_model_solves(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / "exported_contact.in"
            make_solvable_exporter().export(str(input_path))
            input_text = input_path.read_text(encoding="utf-8")
            continuum_records = records_with_keyword(
                input_text, "Quad1PlaneStrain"
            )
            carrier_records = records_with_keyword(
                input_text, "StructuralContactElement_LineLin"
            )
            self.assertTrue(continuum_records)
            self.assertTrue(
                all(" nlgeo 1" in record for record in continuum_records)
            )
            self.assertTrue(carrier_records)
            self.assertTrue(
                all(" nlgeo " not in record for record in carrier_records)
            )
            result = subprocess.run(
                [OOFEM_BINARY, "-f", str(input_path)],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            diagnostics = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, diagnostics)
            self.assertIn("0 error(s)", result.stdout)
            output_path = input_path.with_suffix(".out")
            self.assertTrue(output_path.is_file(), diagnostics)
            output = output_path.read_text(encoding="utf-8")
            reactions = [
                abs(float(value))
                for value in re.findall(
                    r"reaction\s+[^\n]*?([+\-]\d+(?:\.\d*)?[eE][+\-]?\d+)",
                    output,
                    re.I,
                )
            ]
            self.assertTrue(reactions, output)
            self.assertGreater(max(reactions), 1.0e-8)

    def test_exported_small_strain_contact_solves_without_nlgeo(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / "small_strain_contact.in"
            make_small_strain_solvable_exporter().export(str(input_path))
            input_text = input_path.read_text(encoding="utf-8")
            self.assertNotIn(" nlgeo ", input_text)

            result = subprocess.run(
                [OOFEM_BINARY, "-f", str(input_path)],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            diagnostics = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, diagnostics)
            self.assertIn("0 error(s)", result.stdout)
            output_path = input_path.with_suffix(".out")
            self.assertTrue(output_path.is_file(), diagnostics)
            output = output_path.read_text(encoding="utf-8")
            reactions = [
                abs(float(value))
                for value in re.findall(
                    r"reaction\s+[^\n]*?([+\-]\d+(?:\.\d*)?[eE][+\-]?\d+)",
                    output,
                    re.I,
                )
            ]
            self.assertTrue(reactions, output)
            self.assertGreater(max(reactions), 1.0e-8)


@unittest.skipUnless(
    OOFEM_BINARY,
    "set OOFEM_BIN to test contact fields 150-152",
)
class CurrentContactResultTests(unittest.TestCase):
    def test_current_contact_gap_pressure_and_status_vtk_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / "contact_results.in"
            exporter = make_solvable_exporter()
            exporter.solver_settings.update(
                {
                    "vtk": True,
                    "vtk_record": (
                        "vtkxml tstep_all domain_all primvars 1 1 "
                        "cellvars 4 1 150 151 152"
                    ),
                }
            )
            exporter.export(str(input_path))
            result = subprocess.run(
                [OOFEM_BINARY, "-f", str(input_path)],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            diagnostics = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, diagnostics)
            self.assertIn("0 error(s)", result.stdout)
            vtk_files = list(pathlib.Path(directory).glob("*.vtu"))
            self.assertTrue(vtk_files, diagnostics)
            vtk_text = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in vtk_files
            )
            for field_name in ("ContactNormalGap", "ContactPressure", "ContactStatus"):
                self.assertIn(field_name, vtk_text)


if __name__ == "__main__":
    unittest.main()
