"""Create a grouped 2D cantilever mesh for exercising the OOFEM plugin.

Run in SALOME's Python console with::

    exec(open("/absolute/path/to/salome_plane_stress_groups_test.py").read())

The mesh contains two non-overlapping face groups for material assignment,
one node group for the support, one node group for an optional point load, and
one edge-element group for a distributed boundary load.
"""

import salome

salome.salome_init()

import SMESH
from salome.smesh import smeshBuilder


NX = 6
NY = 2
LENGTH = 6.0
HEIGHT = 2.0

smesh_builder = smeshBuilder.New()
mesh = smesh_builder.Mesh()

nodes = []
for row in range(NY + 1):
    y = HEIGHT * row / NY
    node_row = []
    for column in range(NX + 1):
        x = LENGTH * column / NX
        node_row.append(mesh.AddNode(x, y, 0.0))
    nodes.append(node_row)

left_faces = []
right_faces = []
for row in range(NY):
    for column in range(NX):
        face = mesh.AddFace(
            [
                nodes[row][column],
                nodes[row][column + 1],
                nodes[row + 1][column + 1],
                nodes[row + 1][column],
            ]
        )
        if column < NX // 2:
            left_faces.append(face)
        else:
            right_faces.append(face)

# Boundary elements are separate SMESH edges. The exporter matches their node
# pairs to the local boundaries of the material-assigned quadrangles.
right_edges = []
for row in range(NY):
    right_edges.append(mesh.AddEdge([nodes[row][NX], nodes[row + 1][NX]]))

material_left = mesh.CreateEmptyGroup(SMESH.FACE, "MAT_LEFT_FACES")
material_left.Add(left_faces)

material_right = mesh.CreateEmptyGroup(SMESH.FACE, "MAT_RIGHT_FACES")
material_right.Add(right_faces)

fixed_left = mesh.CreateEmptyGroup(SMESH.NODE, "BC_FIXED_LEFT")
fixed_left.Add([nodes[row][0] for row in range(NY + 1)])

tip_node = mesh.CreateEmptyGroup(SMESH.NODE, "LOAD_TOP_RIGHT_NODE")
tip_node.Add([nodes[NY][NX]])

loaded_edge = mesh.CreateEmptyGroup(SMESH.EDGE, "LOAD_RIGHT_EDGE")
loaded_edge.Add(right_edges)

smesh_builder.SetName(mesh.GetMesh(), "OOFEM_Plane_Stress_Group_Test")
salome.sg.updateObjBrowser()

print("Created OOFEM_Plane_Stress_Group_Test")
print("  nodes: {}".format((NX + 1) * (NY + 1)))
print("  quadrangles: {} ({} left + {} right)".format(
    NX * NY, len(left_faces), len(right_faces)
))
print("  material groups: MAT_LEFT_FACES, MAT_RIGHT_FACES")
print("  support group: BC_FIXED_LEFT")
print("  distributed-load group: LOAD_RIGHT_EDGE")
print("  optional point-load group: LOAD_TOP_RIGHT_NODE")
print("Suggested plugin setup:")
print("  Quadrangle -> PlaneStress2d")
print("  MAT_LEFT_FACES: 2D plane stress, E=200000, nu=0.30, t=0.10")
print("  MAT_RIGHT_FACES: 2D plane stress, E=70000, nu=0.33, t=0.10")
print("  BC_FIXED_LEFT: displacement DOF 1 = 0 and DOF 2 = 0")
print("  LOAD_RIGHT_EDGE: surface load DOF 2 = -100")
