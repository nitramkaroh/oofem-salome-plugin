import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

OOFEM_BINARY = os.environ.get("OOFEM_BIN") or shutil.which("oofem")


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

fake_smesh = types.ModuleType("SMESH")
fake_smesh.NODE = 0
fake_smesh.EDGE = 1
fake_smesh.FACE = 2
fake_smesh.VOLUME = 3
fake_smesh.EntityType = types.SimpleNamespace(_items=ENTITY_ITEMS)

fake_module = types.ModuleType("OOFEMSalomePlugin.OOFEMModule")
fake_module.getModule = lambda: None

previous_smesh = sys.modules.get("SMESH")
previous_module = sys.modules.get("OOFEMSalomePlugin.OOFEMModule")
sys.modules["SMESH"] = fake_smesh
sys.modules["OOFEMSalomePlugin.OOFEMModule"] = fake_module
try:
    from OOFEMSalomePlugin.OOFEMExporter import (  # noqa: E402
        OOFEMExporter,
        OOFEMValidationError,
    )
finally:
    if previous_smesh is None:
        sys.modules.pop("SMESH", None)
    else:
        sys.modules["SMESH"] = previous_smesh
    if previous_module is None:
        sys.modules.pop("OOFEMSalomePlugin.OOFEMModule", None)
    else:
        sys.modules["OOFEMSalomePlugin.OOFEMModule"] = previous_module


def boundary_templates():
    path = REPOSITORY_ROOT / "src" / "OOFEMSalomePlugin" / "OOFEMBCs.json"
    return json.loads(path.read_text(encoding="utf-8"))["boundary_conditions"]


class FakeMesh:
    def __init__(self, entity_type):
        self.entity_type = entity_type

    def GetElementGeomType(self, element_id):
        return self.entity_type


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


class FakeStructuralMesh:
    def __init__(self, groups, connectivity, element_types, coordinates):
        self.groups = groups
        self.connectivity = connectivity
        self.element_types = element_types
        self.coordinates = coordinates

    def GetGroups(self):
        return self.groups

    def GetElementGeomType(self, element_id):
        return self.element_types[element_id]

    def GetNodesId(self):
        return list(self.coordinates)

    def GetNodeXYZ(self, node_id):
        return self.coordinates[node_id]

    def GetElemNodes(self, element_id, *unused):
        return self.connectivity[element_id]

    def NbElements(self):
        return len(self.element_types)


def truss_model():
    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("bars", fake_smesh.EDGE, [100]),
            FakeGroup("fixed", fake_smesh.NODE, [10]),
            FakeGroup("roller", fake_smesh.NODE, [20]),
            FakeGroup("loaded", fake_smesh.NODE, [20]),
        ],
        connectivity={100: [10, 20]},
        element_types={100: ENTITY_ITEMS[0]},
        coordinates={10: (0.0, 0.0, 0.0), 20: (1.0, 0.0, 0.0)},
    )
    materials = [
        {
            "id": "material-1",
            "name": "steel bar",
            "oofem_type": "Truss",
            "assigned_group": "bars",
            "params": {"E": 200.0, "A": 2.0},
        }
    ]
    bcs = [
        {
            "id": "fixed-{}".format(dof),
            "name": "fix node 1 dof {}".format(dof),
            "oofem_type": "Displacement",
            "assigned_group": "fixed",
            "params": {"dof": dof, "val": 0.0},
        }
        for dof in (1, 2, 3)
    ]
    bcs.extend(
        {
            "id": "roller-{}".format(dof),
            "name": "fix node 2 dof {}".format(dof),
            "oofem_type": "Displacement",
            "assigned_group": "roller",
            "params": {"dof": dof, "val": 0.0},
        }
        for dof in (2, 3)
    )
    bcs.append(
        {
            "id": "load-1",
            "name": "axial load",
            "oofem_type": "NodalLoad",
            "assigned_group": "loaded",
            "params": {"dof": 1, "val": 10.0},
        }
    )
    return mesh, {"Segment": "Truss3D"}, materials, bcs


def plane_stress_model():
    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("sheet", fake_smesh.FACE, [100]),
            FakeGroup("fixed", fake_smesh.NODE, [10]),
            FakeGroup("roller", fake_smesh.NODE, [20]),
            FakeGroup("traction-edge", fake_smesh.EDGE, [500]),
        ],
        connectivity={
            100: [10, 20, 30],
            500: [20, 30],
        },
        element_types={100: ENTITY_ITEMS[2]},
        coordinates={
            10: (0.0, 0.0, 0.0),
            20: (1.0, 0.0, 0.0),
            30: (0.0, 1.0, 0.0),
        },
    )
    materials = [
        {
            "id": "material-2d",
            "name": "sheet",
            "oofem_type": "ElasticIsotropic2d",
            "assigned_group": "sheet",
            "params": {"E": 1000.0, "nu": 0.25, "t": 1.0},
        }
    ]
    bcs = [
        {
            "id": "fixed-{}".format(dof),
            "name": "fixed {}".format(dof),
            "oofem_type": "Displacement",
            "assigned_group": "fixed",
            "params": {"dof": dof, "val": 0.0},
        }
        for dof in (1, 2)
    ]
    bcs.extend(
        [
            {
                "id": "roller-y",
                "name": "roller y",
                "oofem_type": "Displacement",
                "assigned_group": "roller",
                "params": {"dof": 2, "val": 0.0},
            },
            {
                "id": "edge-load",
                "name": "edge traction",
                "oofem_type": "SurfaceLoad",
                "assigned_group": "traction-edge",
                "params": {"dof": 1, "val": 1.0},
            },
        ]
    )
    return mesh, {"Triangle": "TrPlaneStress2d"}, materials, bcs


def plane_stress_quad_model():
    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("sheet", fake_smesh.FACE, [100]),
            FakeGroup("fixed-edge-nodes", fake_smesh.NODE, [10, 40]),
            FakeGroup("traction-edge", fake_smesh.EDGE, [500]),
        ],
        connectivity={
            100: [10, 20, 30, 40],
            500: [20, 30],
        },
        element_types={100: ENTITY_ITEMS[6]},
        coordinates={
            10: (0.0, 0.0, 0.0),
            20: (1.0, 0.0, 0.0),
            30: (1.0, 1.0, 0.0),
            40: (0.0, 1.0, 0.0),
        },
    )
    materials = [
        {
            "id": "material-quad",
            "name": "quad sheet",
            "oofem_type": "ElasticIsotropic2d",
            "assigned_group": "sheet",
            "params": {"E": 1000.0, "nu": 0.25, "t": 1.0},
        }
    ]
    bcs = [
        {
            "id": "fixed-edge-{}".format(dof),
            "name": "fixed edge {}".format(dof),
            "oofem_type": "Displacement",
            "assigned_group": "fixed-edge-nodes",
            "params": {"dof": dof, "val": 0.0},
        }
        for dof in (1, 2)
    ]
    bcs.append(
        {
            "id": "quad-edge-load",
            "name": "quad edge traction",
            "oofem_type": "SurfaceLoad",
            "assigned_group": "traction-edge",
            "params": {"dof": 1, "val": 1.0},
        }
    )
    return mesh, {"Quadrangle": "PlaneStress2d"}, materials, bcs


def tetra_model():
    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("solid", fake_smesh.VOLUME, [100]),
            FakeGroup("anchor", fake_smesh.NODE, [10]),
            FakeGroup("guide-yz", fake_smesh.NODE, [20]),
            FakeGroup("guide-z", fake_smesh.NODE, [30]),
            FakeGroup("loaded-face", fake_smesh.FACE, [500]),
        ],
        connectivity={
            100: [10, 20, 30, 40],
            500: [20, 30, 40],
        },
        element_types={100: ENTITY_ITEMS[7]},
        coordinates={
            10: (0.0, 0.0, 0.0),
            20: (1.0, 0.0, 0.0),
            30: (0.0, 1.0, 0.0),
            40: (0.0, 0.0, 1.0),
        },
    )
    materials = [
        {
            "id": "material-3d",
            "name": "solid",
            "oofem_type": "ElasticIsotropic3d",
            "assigned_group": "solid",
            "params": {"E": 1000.0, "nu": 0.25},
        }
    ]
    bcs = [
        {
            "id": "anchor-{}".format(dof),
            "name": "anchor {}".format(dof),
            "oofem_type": "Displacement",
            "assigned_group": "anchor",
            "params": {"dof": dof, "val": 0.0},
        }
        for dof in (1, 2, 3)
    ]
    bcs.extend(
        [
            {
                "id": "guide-y",
                "name": "guide y",
                "oofem_type": "Displacement",
                "assigned_group": "guide-yz",
                "params": {"dof": 2, "val": 0.0},
            },
            {
                "id": "guide-z-20",
                "name": "guide z node 20",
                "oofem_type": "Displacement",
                "assigned_group": "guide-yz",
                "params": {"dof": 3, "val": 0.0},
            },
            {
                "id": "guide-z-30",
                "name": "guide z node 30",
                "oofem_type": "Displacement",
                "assigned_group": "guide-z",
                "params": {"dof": 3, "val": 0.0},
            },
            {
                "id": "surface-load",
                "name": "surface traction",
                "oofem_type": "SurfaceLoad",
                "assigned_group": "loaded-face",
                "params": {"dof": 3, "val": -1.0},
            },
        ]
    )
    return mesh, {"Tetrahedron": "LTRSpace"}, materials, bcs



def quad1_plane_strain_model():
    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("sheet", fake_smesh.FACE, [100]),
            FakeGroup("fixed-edge-nodes", fake_smesh.NODE, [10, 40]),
            FakeGroup("traction-edge", fake_smesh.EDGE, [500]),
        ],
        connectivity={
            100: [10, 20, 30, 40],
            500: [20, 30],
        },
        element_types={100: ENTITY_ITEMS[6]},
        coordinates={
            10: (0.0, 0.0, 0.0),
            20: (1.0, 0.0, 0.0),
            30: (1.0, 1.0, 0.0),
            40: (0.0, 1.0, 0.0),
        },
    )
    materials = [
        {
            "id": "material-planestrain",
            "name": "plane strain sheet",
            "oofem_type": "ElasticIsotropic2d",
            "assigned_group": "sheet",
            "params": {"E": 1000.0, "nu": 0.25, "t": 1.0},
        }
    ]
    bcs = [
        {
            "id": "fixed-edge-{}".format(dof),
            "name": "fixed edge {}".format(dof),
            "oofem_type": "Displacement",
            "assigned_group": "fixed-edge-nodes",
            "params": {"dof": dof, "val": 0.0},
        }
        for dof in (1, 2)
    ]
    bcs.append(
        {
            "id": "quad-edge-load",
            "name": "quad edge traction",
            "oofem_type": "SurfaceLoad",
            "assigned_group": "traction-edge",
            "params": {"dof": 1, "val": 1.0},
        }
    )
    return mesh, {"Quadrangle": "Quad1PlaneStrain"}, materials, bcs


def qplanestrain_model():
    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("sheet", fake_smesh.FACE, [100]),
            FakeGroup("fixed-edge-nodes", fake_smesh.NODE, [10, 40, 80]),
            FakeGroup("traction-edge", fake_smesh.EDGE, [500]),
        ],
        connectivity={
            100: [10, 20, 30, 40, 50, 60, 70, 80],
            500: [20, 30, 60],
        },
        element_types={100: ENTITY_ITEMS[8]},
        coordinates={
            10: (0.0, 0.0, 0.0),
            20: (1.0, 0.0, 0.0),
            30: (1.0, 1.0, 0.0),
            40: (0.0, 1.0, 0.0),
            50: (0.5, 0.0, 0.0),
            60: (1.0, 0.5, 0.0),
            70: (0.5, 1.0, 0.0),
            80: (0.0, 0.5, 0.0),
        },
    )
    materials = [
        {
            "id": "material-qplanestrain",
            "name": "quadratic plane strain sheet",
            "oofem_type": "ElasticIsotropic2d",
            "assigned_group": "sheet",
            "params": {"E": 1000.0, "nu": 0.25, "t": 1.0},
        }
    ]
    bcs = [
        {
            "id": "fixed-edge-{}".format(dof),
            "name": "fixed edge {}".format(dof),
            "oofem_type": "Displacement",
            "assigned_group": "fixed-edge-nodes",
            "params": {"dof": dof, "val": 0.0},
        }
        for dof in (1, 2)
    ]
    bcs.append(
        {
            "id": "quad-edge-load",
            "name": "quadratic quad edge traction",
            "oofem_type": "SurfaceLoad",
            "assigned_group": "traction-edge",
            "params": {"dof": 1, "val": 1.0},
        }
    )
    return mesh, {"Quadrangle8": "QPlaneStrain"}, materials, bcs


def lspace_model():
    """Unit cube: node numbering follows LSpace's local corner topology exactly
    (corners 1-4 on the x=0..1? no - see coordinates: 1-4 span z=1, 5-8 span z=0,
    with 1<->5, 2<->6, 3<->7, 4<->8 vertical correspondence), so the connectivity
    below matches OOFEM's FEI3dHexaLin node order with a positive Jacobian.
    """
    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("solid", fake_smesh.VOLUME, [100]),
            FakeGroup("anchor", fake_smesh.NODE, [1]),
            FakeGroup("roller-x", fake_smesh.NODE, [2, 5, 6]),
            FakeGroup("guide-z", fake_smesh.NODE, [2]),
            FakeGroup("loaded-face", fake_smesh.FACE, [500]),
        ],
        connectivity={
            100: [1, 2, 3, 4, 5, 6, 7, 8],
            500: [3, 4, 8, 7],
        },
        element_types={100: ENTITY_ITEMS[4]},
        coordinates={
            1: (0.0, 0.0, 1.0),
            2: (0.0, 1.0, 1.0),
            3: (1.0, 1.0, 1.0),
            4: (1.0, 0.0, 1.0),
            5: (0.0, 0.0, 0.0),
            6: (0.0, 1.0, 0.0),
            7: (1.0, 1.0, 0.0),
            8: (1.0, 0.0, 0.0),
        },
    )
    materials = [
        {
            "id": "material-lspace",
            "name": "cube",
            "oofem_type": "ElasticIsotropic3d",
            "assigned_group": "solid",
            "params": {"E": 1000.0, "nu": 0.25},
        }
    ]
    bcs = [
        {
            "id": "anchor-{}".format(dof),
            "name": "anchor {}".format(dof),
            "oofem_type": "Displacement",
            "assigned_group": "anchor",
            "params": {"dof": dof, "val": 0.0},
        }
        for dof in (1, 2, 3)
    ]
    bcs.append(
        {
            "id": "roller-x",
            "name": "x=0 face rollers",
            "oofem_type": "Displacement",
            "assigned_group": "roller-x",
            "params": {"dof": 1, "val": 0.0},
        }
    )
    bcs.append(
        {
            "id": "guide-z",
            "name": "guide z at node 2",
            "oofem_type": "Displacement",
            "assigned_group": "guide-z",
            "params": {"dof": 3, "val": 0.0},
        }
    )
    bcs.append(
        {
            "id": "surface-load",
            "name": "x=1 face traction",
            "oofem_type": "SurfaceLoad",
            "assigned_group": "loaded-face",
            "params": {"dof": 1, "val": 100.0},
        }
    )
    return mesh, {"Hexahedron": "LSpace"}, materials, bcs


def qspace_model():
    """Same unit cube as lspace_model, with the 12 midside nodes OOFEM's
    FEI3dHexaQuad expects (9-20) placed at exact edge midpoints, so the
    element is geometrically identical to the linear cube."""
    corners = {
        1: (0.0, 0.0, 1.0),
        2: (0.0, 1.0, 1.0),
        3: (1.0, 1.0, 1.0),
        4: (1.0, 0.0, 1.0),
        5: (0.0, 0.0, 0.0),
        6: (0.0, 1.0, 0.0),
        7: (1.0, 1.0, 0.0),
        8: (1.0, 0.0, 0.0),
    }

    def midpoint(a, b):
        pa, pb = corners[a], corners[b]
        return tuple((pa[i] + pb[i]) / 2.0 for i in range(3))

    midsides = {
        9: midpoint(1, 2),
        10: midpoint(2, 3),
        11: midpoint(3, 4),
        12: midpoint(4, 1),
        13: midpoint(5, 6),
        14: midpoint(6, 7),
        15: midpoint(7, 8),
        16: midpoint(8, 5),
        17: midpoint(1, 5),
        18: midpoint(2, 6),
        19: midpoint(3, 7),
        20: midpoint(4, 8),
    }
    coordinates = dict(corners)
    coordinates.update(midsides)

    mesh = FakeStructuralMesh(
        groups=[
            FakeGroup("solid", fake_smesh.VOLUME, [100]),
            FakeGroup("anchor", fake_smesh.NODE, [1]),
            FakeGroup(
                "roller-x", fake_smesh.NODE, [2, 5, 6, 9, 13, 17, 18]
            ),
            FakeGroup("guide-z", fake_smesh.NODE, [2]),
            FakeGroup("loaded-face", fake_smesh.FACE, [500]),
        ],
        connectivity={
            100: list(range(1, 21)),
            500: [3, 4, 8, 7, 11, 20, 15, 19],
        },
        element_types={100: ENTITY_ITEMS[5]},
        coordinates=coordinates,
    )
    materials = [
        {
            "id": "material-qspace",
            "name": "cube",
            "oofem_type": "ElasticIsotropic3d",
            "assigned_group": "solid",
            "params": {"E": 1000.0, "nu": 0.25},
        }
    ]
    bcs = [
        {
            "id": "anchor-{}".format(dof),
            "name": "anchor {}".format(dof),
            "oofem_type": "Displacement",
            "assigned_group": "anchor",
            "params": {"dof": dof, "val": 0.0},
        }
        for dof in (1, 2, 3)
    ]
    bcs.append(
        {
            "id": "roller-x",
            "name": "x=0 face rollers",
            "oofem_type": "Displacement",
            "assigned_group": "roller-x",
            "params": {"dof": 1, "val": 0.0},
        }
    )
    bcs.append(
        {
            "id": "guide-z",
            "name": "guide z at node 2",
            "oofem_type": "Displacement",
            "assigned_group": "guide-z",
            "params": {"dof": 3, "val": 0.0},
        }
    )
    bcs.append(
        {
            "id": "surface-load",
            "name": "x=1 face traction",
            "oofem_type": "SurfaceLoad",
            "assigned_group": "loaded-face",
            "params": {"dof": 1, "val": 100.0},
        }
    )
    return mesh, {"Hexahedron20": "QSpace"}, materials, bcs


def idm1_truss_model():
    mesh, _, _, bcs = truss_model()
    materials = [
        {
            "id": "material-idm1",
            "name": "damage bar (undamaged regime)",
            "oofem_type": "idm1",
            "assigned_group": "bars",
            "params": {
                "E": 200.0,
                "nu": 0.2,
                "A": 2.0,
                "e0": 1.0,
                "ef": 2.0,
                "equivstraintype": 0,
                "damlaw": 0,
            },
        }
    ]
    return mesh, {"Segment": "Truss3D"}, materials, bcs


def misesmat_truss_model():
    mesh, _, _, bcs = truss_model()
    materials = [
        {
            "id": "material-misesmat",
            "name": "plastic bar (elastic regime)",
            "oofem_type": "misesmat",
            "assigned_group": "bars",
            "params": {"E": 200.0, "nu": 0.2, "A": 2.0, "sig0": 1.0e12, "h": 0.0},
        }
    ]
    return mesh, {"Segment": "Truss3D"}, materials, bcs


def ogden_quad_model():
    mesh, elem_map, _, bcs = quad1_plane_strain_model()
    materials = [
        {
            "id": "material-ogden",
            "name": "ogden sheet",
            "oofem_type": "ogdencompressiblemat",
            "assigned_group": "sheet",
            "params": {"k": 0.0, "alpha1": 2.0, "mu1": 20.0, "t": 1.0},
        }
    ]
    return mesh, elem_map, materials, bcs


def mooney_rivlin_quad_model():
    mesh, elem_map, _, bcs = quad1_plane_strain_model()
    materials = [
        {
            "id": "material-mooneyrivlin",
            "name": "mooney-rivlin sheet",
            "oofem_type": "mooneyrivlincompressiblemat",
            "assigned_group": "sheet",
            "params": {"k": 0.0, "c1": 10.0, "c2": 0.0, "t": 1.0},
        }
    ]
    return mesh, elem_map, materials, bcs


class ExporterTypeMappingTests(unittest.TestCase):
    def make_exporter(self):
        exporter = OOFEMExporter.__new__(OOFEMExporter)
        exporter.debug_console = None
        return exporter

    def test_builds_map_from_detailed_entity_type_variants(self):
        exporter = self.make_exporter()
        type_map = exporter._build_salome_type_map()
        self.assertEqual(type_map[1], "Segment")
        self.assertEqual(type_map[2], "Segment")
        self.assertEqual(type_map[3], "Triangle")
        self.assertEqual(type_map[4], "Triangle")
        self.assertEqual(type_map[5], "Hexahedron")
        self.assertEqual(type_map[6], "Hexahedron20")
        self.assertEqual(type_map[7], "Quadrangle")
        self.assertEqual(type_map[8], "Tetrahedron")

    def test_uses_geometry_entity_value_without_offset(self):
        exporter = self.make_exporter()
        exporter.mesh = FakeMesh(EnumItem("Entity_Triangle", 3))
        exporter.SALOME_TYPE_NAMES = {3: "Triangle"}
        exporter.elem_to_groups = {}
        exporter.group_to_mat = {}
        exporter.elem_map = {"Triangle": "tr1"}
        self.assertEqual(exporter._get_oofem_element_type(42), "tr1")


class ExporterValidationTests(unittest.TestCase):
    def test_remaps_sparse_salome_ids_and_translates_triangle_edge(self):
        exporter = OOFEMExporter(
            *plane_stress_model(), boundary_templates(), solver_settings={"vtk": False}
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "plane.in"
            exporter.export(str(path))
            model = path.read_text(encoding="utf-8")
        self.assertIn("node 1 coords 2 0 0", model)
        self.assertIn("TrPlaneStress2d 1 nodes 3 1 2 3", model)
        self.assertIn("ConstantEdgeLoad", model)
        self.assertRegex(model, r"Set \d+ elementEdges 2 1 2")

    def test_translates_tetra_face_to_ltrspace_boundary_number(self):
        exporter = OOFEMExporter(
            *tetra_model(), boundary_templates(), solver_settings={"vtk": False}
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "tetra.in"
            exporter.export(str(path))
            model = path.read_text(encoding="utf-8")
        self.assertIn("LTRSpace 1 nodes 4 1 2 3 4", model)
        self.assertIn("ConstantSurfaceLoad", model)
        self.assertRegex(model, r"Set \d+ elementBoundaries 2 1 3")

    def test_rejects_overlapping_material_groups(self):
        mesh, mapping, materials, bcs = truss_model()
        mesh.groups.insert(1, FakeGroup("second-bars", fake_smesh.EDGE, [100]))
        materials.append(
            {
                "id": "other",
                "name": "other",
                "oofem_type": "Truss",
                "assigned_group": "second-bars",
                "params": {"E": 1.0, "A": 1.0},
            }
        )
        exporter = OOFEMExporter(mesh, mapping, materials, bcs, boundary_templates())
        with self.assertRaisesRegex(OOFEMValidationError, "overlapping material groups"):
            exporter.validate()

    def test_rejects_surface_load_on_interior_shared_facet(self):
        # Two triangles sharing edge (20, 30): a surface load on that
        # interior facet must be rejected, not silently applied from both
        # sides (which would double the effective load with no warning).
        mesh = FakeStructuralMesh(
            groups=[
                FakeGroup("sheet", fake_smesh.FACE, [100, 101]),
                FakeGroup("fixed", fake_smesh.NODE, [10]),
                FakeGroup("shared-edge", fake_smesh.EDGE, [500]),
            ],
            connectivity={
                100: [10, 20, 30],
                101: [20, 30, 40],
                500: [20, 30],
            },
            element_types={
                100: ENTITY_ITEMS[2],
                101: ENTITY_ITEMS[2],
            },
            coordinates={
                10: (0.0, 0.0, 0.0),
                20: (1.0, 0.0, 0.0),
                30: (0.0, 1.0, 0.0),
                40: (1.0, 1.0, 0.0),
            },
        )
        materials = [
            {
                "id": "material-2d",
                "name": "sheet",
                "oofem_type": "ElasticIsotropic2d",
                "assigned_group": "sheet",
                "params": {"E": 1000.0, "nu": 0.25, "t": 1.0},
            }
        ]
        bcs = [
            {
                "id": "fixed-1",
                "name": "fixed",
                "oofem_type": "Displacement",
                "assigned_group": "fixed",
                "params": {"dof": 1, "val": 0.0},
            },
            {
                "id": "shared-edge-load",
                "name": "interior edge load",
                "oofem_type": "SurfaceLoad",
                "assigned_group": "shared-edge",
                "params": {"dof": 1, "val": 1.0},
            },
        ]
        exporter = OOFEMExporter(
            mesh, {"Triangle": "TrPlaneStress2d"}, materials, bcs, boundary_templates()
        )
        with self.assertRaisesRegex(
            OOFEMValidationError, "shared by 2 exported material elements"
        ):
            exporter.validate()

    def test_rejects_missing_cross_section_area_instead_of_defaulting(self):
        mesh, mapping, materials, bcs = truss_model()
        materials[0]["params"].pop("A")
        exporter = OOFEMExporter(mesh, mapping, materials, bcs, boundary_templates())
        with self.assertRaisesRegex(OOFEMValidationError, "requires a positive 'area' value"):
            exporter.validate()

    def test_rejects_missing_cross_section_thickness_instead_of_defaulting(self):
        mesh, mapping, materials, bcs = plane_stress_model()
        materials[0]["params"].pop("t")
        exporter = OOFEMExporter(mesh, mapping, materials, bcs, boundary_templates())
        with self.assertRaisesRegex(OOFEMValidationError, "requires a positive 'thick' value"):
            exporter.validate()

    def test_rejects_malformed_vtk_record(self):
        mesh, mapping, materials, bcs = truss_model()
        exporter = OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={
                "vtk": True,
                "vtk_record": "vtkxml tstep_all domain_all primvars 2 1 cellvars 1 1",
            },
        )
        with self.assertRaisesRegex(
            OOFEMValidationError, "declares 2 'primvars' but does not provide"
        ):
            exporter.validate()

    def test_accepts_well_formed_vtk_record(self):
        mesh, mapping, materials, bcs = truss_model()
        exporter = OOFEMExporter(
            mesh,
            mapping,
            materials,
            bcs,
            boundary_templates(),
            solver_settings={
                "vtk": True,
                "vtk_record": (
                    "vtkxml tstep_all domain_all primvars 1 1 "
                    "cellvars 4 1 150 151 152"
                ),
            },
        )
        exporter.validate()


@unittest.skipUnless(
    OOFEM_BINARY,
    "set OOFEM_BIN to run generated inputs with a real OOFEM solver",
)
class OOFEMSolverIntegrationTests(unittest.TestCase):
    def solve(
        self,
        model_factory,
        expected_records,
        solver_settings=None,
        cross_sections=None,
    ):
        exporter = OOFEMExporter(
            *model_factory(),
            boundary_templates(),
            solver_settings=solver_settings,
            cross_sections=cross_sections
        )
        with tempfile.TemporaryDirectory() as directory:
            input_path = pathlib.Path(directory) / "salome_model.in"
            exporter.export(str(input_path))
            generated_input = input_path.read_text(encoding="utf-8")
            for expected in expected_records:
                self.assertIn(expected, generated_input)

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
            self.assertTrue(input_path.with_suffix(".out").is_file())
            self.assertTrue(
                list(pathlib.Path(directory).glob("salome_model.out.m*.vtu")),
                "VTK preset produced no .vtu output",
            )
            return input_path.with_suffix(".out").read_text(encoding="utf-8")

    def _displacement(self, output, node_number, dof=1):
        node = re.search(
            r"Node\s+{}\s+\([^)]*\):(.*?)(?:Node|Element output:)".format(node_number),
            output,
            re.S,
        )
        self.assertIsNotNone(node, output)
        displacement = re.search(
            r"dof\s+{}\s+d\s+([+\-0-9.eE]+)".format(dof), node.group(1)
        )
        self.assertIsNotNone(displacement, node.group(1))
        return float(displacement.group(1))

    def _assert_displacement(self, output, node_number, dof, expected, places=10):
        self.assertAlmostEqual(
            self._displacement(output, node_number, dof), expected, places=places
        )

    def _assert_nonzero_displacement(self, output, node_number, dof=1):
        self.assertGreater(abs(self._displacement(output, node_number, dof)), 1.0e-12)

    def _assert_dof1_displacement(self, output, node_number, expected, places=10):
        self._assert_displacement(output, node_number, 1, expected, places)

    def test_exported_truss_solves_to_analytical_displacement(self):
        output = self.solve(
            truss_model,
            ["StaticStructural nsteps 1 nmodules 1", "Truss3D 1 nodes 2 1 2"],
        )
        self._assert_dof1_displacement(output, 2, 0.025)

    def test_exported_plane_stress_edge_load_solves(self):
        output = self.solve(
            plane_stress_model,
            [
                "TrPlaneStress2d 1 nodes 3",
                "ConstantEdgeLoad",
                "elementEdges 2 1 2",
            ],
        )
        self._assert_dof1_displacement(output, 2, 1.41421356e-3)
        self._assert_dof1_displacement(output, 3, 3.53553391e-3)

    def test_exported_plane_stress_quad_edge_load_solves(self):
        output = self.solve(
            plane_stress_quad_model,
            ["PlaneStress2d 1 nodes 4", "ConstantEdgeLoad", "elementEdges"],
        )
        self._assert_nonzero_displacement(output, 2)

    def test_exported_tetra_surface_load_solves(self):
        output = self.solve(
            tetra_model,
            ["LTRSpace 1 nodes 4", "ConstantSurfaceLoad", "elementBoundaries"],
        )
        self._assert_nonzero_displacement(output, 4, dof=3)

    def test_exported_quad1_plane_strain_edge_load_solves(self):
        output = self.solve(
            quad1_plane_strain_model,
            ["Quad1PlaneStrain 1 nodes 4", "ConstantEdgeLoad", "elementEdges"],
        )
        self._assert_nonzero_displacement(output, 2)

    def test_exported_qplanestrain_edge_load_solves(self):
        output = self.solve(
            qplanestrain_model,
            ["QPlaneStrain 1 nodes 8", "ConstantEdgeLoad", "elementEdges"],
        )
        self._assert_nonzero_displacement(output, 2)

    def test_exported_lspace_solves_to_analytical_displacement(self):
        output = self.solve(
            lspace_model,
            ["LSpace 1 nodes 8 1 2 3 4 5 6 7 8", "ConstantSurfaceLoad"],
        )
        self._assert_dof1_displacement(output, 3, 0.1)

    def test_exported_qspace_solves_to_analytical_displacement(self):
        output = self.solve(
            qspace_model,
            ["QSpace 1 nodes 20", "ConstantSurfaceLoad"],
        )
        self._assert_dof1_displacement(output, 3, 0.1)

    def test_exported_idm1_below_damage_threshold_matches_elastic_truss(self):
        output = self.solve(
            idm1_truss_model,
            ["StaticStructural nsteps 1 nmodules 1", "idm1 1"],
        )
        self._assert_dof1_displacement(output, 2, 0.025)

    def test_exported_misesmat_below_yield_matches_elastic_truss(self):
        output = self.solve(
            misesmat_truss_model,
            ["StaticStructural nsteps 1 nmodules 1", "misesmat 1"],
        )
        self._assert_dof1_displacement(output, 2, 0.025)

    def test_exported_ogden_hyperelastic_quad_solves(self):
        output = self.solve(
            ogden_quad_model,
            [
                "ogdencompressiblemat 1",
                "Quad1PlaneStrain 1 nodes 4 1 2 3 4 nlgeo 1",
                "elementEdges",
            ],
            solver_settings={"vtk": True, "nsteps": 5},
            # nlgeo has no global default; hyperelastic materials need it
            # enabled explicitly on the cross section.
            cross_sections=[
                {
                    "id": "cs-ogden",
                    "name": "ogden sheet cross section",
                    "oofem_type": "SimpleCS",
                    "material_id": "material-ogden",
                    "assigned_group": "sheet",
                    "element_options": {"nlgeo": "on"},
                    "params": {"thick": 1.0},
                }
            ],
        )
        self._assert_nonzero_displacement(output, 2)

    def test_exported_mooney_rivlin_hyperelastic_quad_solves(self):
        output = self.solve(
            mooney_rivlin_quad_model,
            [
                "mooneyrivlincompressiblemat 1",
                "Quad1PlaneStrain 1 nodes 4 1 2 3 4 nlgeo 1",
                "elementEdges",
            ],
            solver_settings={"vtk": True, "nsteps": 5},
            # nlgeo has no global default; hyperelastic materials need it
            # enabled explicitly on the cross section.
            cross_sections=[
                {
                    "id": "cs-mooneyrivlin",
                    "name": "mooney-rivlin sheet cross section",
                    "oofem_type": "SimpleCS",
                    "material_id": "material-mooneyrivlin",
                    "assigned_group": "sheet",
                    "element_options": {"nlgeo": "on"},
                    "params": {"thick": 1.0},
                }
            ],
        )
        self._assert_nonzero_displacement(output, 2)


if __name__ == "__main__":
    unittest.main()
