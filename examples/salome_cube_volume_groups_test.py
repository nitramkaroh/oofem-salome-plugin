"""Create a grouped hexahedron cube mesh for exercising the OOFEM plugin's
3D solid workflow (material assignment, a fixed support, a point load, and a
distributed surface load).

Run in SALOME's Python console with::

    exec(open("/absolute/path/to/salome_cube_volume_groups_test.py").read())

The cube is a structured NX x NY x NZ grid of linear hexahedra. Node order
inside every cell follows OOFEM's FEI3dHexaLin corner convention exactly (the
same pattern verified by tests/test_exporter.py::lspace_model): the four
"top" (+Z) corners first, then the four "bottom" (-Z) corners directly below
them, both going around the face in the same rotational sense.

Groups created:
  MAT_FRONT_VOLUMES / MAT_BACK_VOLUMES  - two non-overlapping volume groups
                                           (split along X) for material assignment
  BC_FIXED_X0                           - all nodes on the x=0 face, for a
                                           fully fixed support (DOF 1,2,3 = 0)
  LOAD_POINT_CORNER                     - single node at the far top corner,
                                           for an optional point load
  LOAD_XMAX_FACE                        - boundary quad faces on the x=max
                                           face, for a distributed surface load
"""

import salome

salome.salome_init()

import SMESH
from salome.smesh import smeshBuilder


NX = 2
NY = 2
NZ = 2
LENGTH = 2.0
WIDTH = 2.0
HEIGHT = 2.0

smesh_builder = smeshBuilder.New()
mesh = smesh_builder.Mesh()

nodes = {}
for i in range(NX + 1):
    x = LENGTH * i / NX
    for j in range(NY + 1):
        y = WIDTH * j / NY
        for k in range(NZ + 1):
            z = HEIGHT * k / NZ
            nodes[(i, j, k)] = mesh.AddNode(x, y, z)

front_volumes = []
back_volumes = []
xmax_faces = []
for i in range(NX):
    for j in range(NY):
        for k in range(NZ):
            top = [
                nodes[(i, j, k + 1)],
                nodes[(i, j + 1, k + 1)],
                nodes[(i + 1, j + 1, k + 1)],
                nodes[(i + 1, j, k + 1)],
            ]
            bottom = [
                nodes[(i, j, k)],
                nodes[(i, j + 1, k)],
                nodes[(i + 1, j + 1, k)],
                nodes[(i + 1, j, k)],
            ]
            volume = mesh.AddVolume(top + bottom)
            if i < NX // 2:
                front_volumes.append(volume)
            else:
                back_volumes.append(volume)

            if i == NX - 1:
                # OOFEM's LSpace local face (2, 3, 7, 6) -> connectivity
                # entries 3, 4, 8, 7 (1-based), i.e. the two +X top corners
                # then the two +X bottom corners.
                xmax_faces.append(
                    mesh.AddFace(
                        [
                            nodes[(i + 1, j + 1, k + 1)],
                            nodes[(i + 1, j, k + 1)],
                            nodes[(i + 1, j, k)],
                            nodes[(i + 1, j + 1, k)],
                        ]
                    )
                )

mat_front = mesh.CreateEmptyGroup(SMESH.VOLUME, "MAT_FRONT_VOLUMES")
mat_front.Add(front_volumes)

mat_back = mesh.CreateEmptyGroup(SMESH.VOLUME, "MAT_BACK_VOLUMES")
mat_back.Add(back_volumes)

fixed_x0 = mesh.CreateEmptyGroup(SMESH.NODE, "BC_FIXED_X0")
fixed_x0.Add([nodes[(0, j, k)] for j in range(NY + 1) for k in range(NZ + 1)])

corner_node = mesh.CreateEmptyGroup(SMESH.NODE, "LOAD_POINT_CORNER")
corner_node.Add([nodes[(NX, NY, NZ)]])

xmax_face_group = mesh.CreateEmptyGroup(SMESH.FACE, "LOAD_XMAX_FACE")
xmax_face_group.Add(xmax_faces)

smesh_builder.SetName(mesh.GetMesh(), "OOFEM_Cube_Volume_Group_Test")
salome.sg.updateObjBrowser()

print("Created OOFEM_Cube_Volume_Group_Test")
print("  nodes: {}".format((NX + 1) * (NY + 1) * (NZ + 1)))
print("  hexahedra: {} ({} front + {} back)".format(
    NX * NY * NZ, len(front_volumes), len(back_volumes)
))
print("  material groups: MAT_FRONT_VOLUMES, MAT_BACK_VOLUMES")
print("  support group: BC_FIXED_X0")
print("  point-load group: LOAD_POINT_CORNER")
print("  distributed-load group: LOAD_XMAX_FACE")
print("Suggested plugin setup:")
print("  Hexahedron -> LSpace")
print("  MAT_FRONT_VOLUMES: 3D Isotropic Elastic, E=200000, nu=0.30")
print("  MAT_BACK_VOLUMES: 3D Isotropic Elastic, E=70000, nu=0.33")
print("    (swap either for Isotropic Damage / MisesMat / Ogden / Mooney-Rivlin")
print("     to try the other material templates; Ogden/Mooney-Rivlin need the")
print("     'Large-strain static + VTK (nlgeo)' solver preset)")
print("  BC_FIXED_X0: displacement DOF 1 = 0, DOF 2 = 0, DOF 3 = 0")
print("  LOAD_XMAX_FACE: surface load DOF 1 = -1000 (pressure in -X)")
print("  LOAD_POINT_CORNER: optional nodal load, e.g. DOF 3 = -10")
