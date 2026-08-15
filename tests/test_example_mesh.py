import pathlib
import runpy
import sys
import types
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLE = REPOSITORY_ROOT / "examples" / "salome_plane_stress_groups_test.py"


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


class PlaneStressExampleTests(unittest.TestCase):
    def test_creates_partitioned_faces_and_compatible_boundary_groups(self):
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
            runpy.run_path(str(EXAMPLE), run_name="__main__")
        finally:
            for name, old_module in previous.items():
                if old_module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old_module

        mesh = builder.mesh
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


if __name__ == "__main__":
    unittest.main()
