import pathlib
import runpy
import sys
import types
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLE = REPOSITORY_ROOT / "examples" / "salome_plane_stress_groups_test.py"
CONTACT_EXAMPLE = REPOSITORY_ROOT / "examples" / "salome_contact_2d_test.py"


class FakeGroup:
    def __init__(self, entity_type, name):
        self.entity_type = entity_type
        self.name = name
        self.ids = []

    def Add(self, identifiers):
        self.ids.extend(identifiers)


class FakeMesh:
    def __init__(self):
        self.nodes = {}
        self.faces = {}
        self.edges = {}
        self.groups = {}
        self.next_node = 1
        self.next_element = 1
        self.name = None

    def AddNode(self, x, y, z):
        identifier = self.next_node
        self.next_node += 1
        self.nodes[identifier] = (x, y, z)
        return identifier

    def AddFace(self, connectivity):
        identifier = self.next_element
        self.next_element += 1
        self.faces[identifier] = tuple(connectivity)
        return identifier

    def AddEdge(self, connectivity):
        identifier = self.next_element
        self.next_element += 1
        self.edges[identifier] = tuple(connectivity)
        return identifier

    def CreateEmptyGroup(self, entity_type, name):
        group = FakeGroup(entity_type, name)
        self.groups[name] = group
        return group

    def GetMesh(self):
        return self


class FakeBuilder:
    def __init__(self):
        self.mesh = FakeMesh()

    def Mesh(self):
        return self.mesh

    def SetName(self, mesh, name):
        mesh.name = name


def run_example(path):
    """Execute one SALOME mesh script with the lightweight fake modules."""
    builder = FakeBuilder()
    calls = []

    salome = types.ModuleType("salome")
    salome.__path__ = []
    salome.salome_init = lambda: calls.append("init")
    salome.sg = types.SimpleNamespace(
        updateObjBrowser=lambda: calls.append("update")
    )

    smesh = types.ModuleType("SMESH")
    smesh.NODE = 0
    smesh.EDGE = 1
    smesh.FACE = 2

    salome_smesh = types.ModuleType("salome.smesh")
    salome_smesh.smeshBuilder = types.SimpleNamespace(New=lambda: builder)

    replacements = {
        "salome": salome,
        "SMESH": smesh,
        "salome.smesh": salome_smesh,
    }
    previous = {name: sys.modules.get(name) for name in replacements}
    sys.modules.update(replacements)
    try:
        namespace = runpy.run_path(str(path), run_name="__main__")
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module
    return builder.mesh, calls, smesh, namespace


class PlaneStressExampleTests(unittest.TestCase):
    def test_creates_partitioned_faces_and_compatible_boundary_groups(self):
        mesh, calls, smesh, _namespace = run_example(EXAMPLE)
        self.assertEqual(calls, ["init", "update"])
        self.assertEqual(mesh.name, "OOFEM_Plane_Stress_Group_Test")
        self.assertEqual(len(mesh.nodes), 21)
        self.assertEqual(len(mesh.faces), 12)
        self.assertEqual(len(mesh.edges), 2)

        left = mesh.groups["MAT_LEFT_FACES"]
        right = mesh.groups["MAT_RIGHT_FACES"]
        self.assertEqual(left.entity_type, smesh.FACE)
        self.assertEqual(right.entity_type, smesh.FACE)
        self.assertEqual(len(left.ids), 6)
        self.assertEqual(len(right.ids), 6)
        self.assertFalse(set(left.ids) & set(right.ids))
        self.assertEqual(set(left.ids) | set(right.ids), set(mesh.faces))

        fixed = mesh.groups["BC_FIXED_LEFT"]
        self.assertEqual(fixed.entity_type, smesh.NODE)
        self.assertEqual(len(fixed.ids), 3)
        self.assertTrue(all(mesh.nodes[node][0] == 0.0 for node in fixed.ids))

        tip = mesh.groups["LOAD_TOP_RIGHT_NODE"]
        self.assertEqual(tip.entity_type, smesh.NODE)
        self.assertEqual(len(tip.ids), 1)
        self.assertEqual(mesh.nodes[tip.ids[0]], (6.0, 2.0, 0.0))

        boundary = mesh.groups["LOAD_RIGHT_EDGE"]
        self.assertEqual(boundary.entity_type, smesh.EDGE)
        self.assertEqual(boundary.ids, list(mesh.edges))
        for edge in boundary.ids:
            self.assertTrue(
                all(mesh.nodes[node][0] == 6.0 for node in mesh.edges[edge])
            )


class ContactExampleTests(unittest.TestCase):
    def test_creates_two_independent_overlapping_contact_bodies(self):
        mesh, calls, smesh, namespace = run_example(CONTACT_EXAMPLE)

        self.assertEqual(calls, ["init", "update"])
        self.assertEqual(mesh.name, "OOFEM_Contact_2D_Test")
        self.assertEqual(len(mesh.nodes), 8)
        self.assertEqual(len(mesh.faces), 2)
        self.assertEqual(len(mesh.edges), 2)
        self.assertEqual(
            set(mesh.groups),
            {
                "BODIES",
                "CONTACT_MASTER",
                "CONTACT_SLAVE",
                "FIX_LOWER",
                "FIX_UPPER",
            },
        )

        bodies = mesh.groups["BODIES"]
        self.assertEqual(bodies.entity_type, smesh.FACE)
        self.assertEqual(set(bodies.ids), set(mesh.faces))
        lower_nodes = set(mesh.faces[bodies.ids[0]])
        upper_nodes = set(mesh.faces[bodies.ids[1]])
        self.assertFalse(lower_nodes & upper_nodes)
        self.assertEqual(lower_nodes | upper_nodes, set(mesh.nodes))

        master = mesh.groups["CONTACT_MASTER"]
        slave = mesh.groups["CONTACT_SLAVE"]
        self.assertEqual(master.entity_type, smesh.EDGE)
        self.assertEqual(slave.entity_type, smesh.EDGE)
        self.assertEqual(len(master.ids), 1)
        self.assertEqual(len(slave.ids), 1)

        master_nodes = mesh.edges[master.ids[0]]
        slave_nodes = mesh.edges[slave.ids[0]]
        self.assertTrue(set(master_nodes) <= lower_nodes)
        self.assertTrue(set(slave_nodes) <= upper_nodes)
        self.assertFalse(set(master_nodes) & set(slave_nodes))

        master_coordinates = [mesh.nodes[node] for node in master_nodes]
        slave_coordinates = [mesh.nodes[node] for node in slave_nodes]
        self.assertEqual(
            master_coordinates,
            [(0.5, 0.0, 0.0), (-0.5, 0.0, 0.0)],
        )
        self.assertEqual(
            slave_coordinates,
            [(-0.5, -0.01, 0.0), (0.5, -0.01, 0.0)],
        )
        self.assertAlmostEqual(
            master_coordinates[0][1] - slave_coordinates[0][1],
            namespace["INITIAL_OVERLAP"],
        )

        fixed_lower = mesh.groups["FIX_LOWER"]
        fixed_upper = mesh.groups["FIX_UPPER"]
        self.assertEqual(fixed_lower.entity_type, smesh.NODE)
        self.assertEqual(fixed_upper.entity_type, smesh.NODE)
        self.assertEqual(len(fixed_lower.ids), 2)
        self.assertEqual(len(fixed_upper.ids), 2)
        self.assertTrue(
            all(mesh.nodes[node][1] == -1.0 for node in fixed_lower.ids)
        )
        self.assertTrue(
            all(mesh.nodes[node][1] == 0.99 for node in fixed_upper.ids)
        )


if __name__ == "__main__":
    unittest.main()
