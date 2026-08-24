"""Create a minimal two-body plane-strain contact model for the OOFEM plugin.

Run in SALOME's Python console with::

    exec(open("/absolute/path/to/salome_contact_2d_test.py").read())

The mesh deliberately contains two independent linear quadrangles.  Their
interface nodes are *not* shared: the upper body starts 0.01 below the top of
the lower body, giving a small initial penetration that exercises penalty
contact without an additional load.  The contact-edge orientation follows the
outward normal of each body (master upward, slave downward).

Groups created:
  BODIES          - both material quadrangles
  CONTACT_MASTER  - upper edge of the lower body
  CONTACT_SLAVE   - lower edge of the upper body
  FIX_LOWER       - lower body's remote edge nodes
  FIX_UPPER       - upper body's remote edge nodes
"""

import salome

salome.salome_init()

import SMESH
from salome.smesh import smeshBuilder


HALF_WIDTH = 0.5
BODY_HEIGHT = 1.0
INITIAL_OVERLAP = 0.01

smesh_builder = smeshBuilder.New()
mesh = smesh_builder.Mesh()

# Lower body, counter-clockwise Quad1PlaneStrain connectivity.
lower_left = mesh.AddNode(-HALF_WIDTH, -BODY_HEIGHT, 0.0)
lower_right = mesh.AddNode(HALF_WIDTH, -BODY_HEIGHT, 0.0)
master_right = mesh.AddNode(HALF_WIDTH, 0.0, 0.0)
master_left = mesh.AddNode(-HALF_WIDTH, 0.0, 0.0)
lower_body = mesh.AddFace(
    [lower_left, lower_right, master_right, master_left]
)

# Upper body uses distinct interface nodes.  Its lower edge lies inside the
# lower body by INITIAL_OVERLAP, while its face connectivity remains CCW.
slave_left = mesh.AddNode(-HALF_WIDTH, -INITIAL_OVERLAP, 0.0)
slave_right = mesh.AddNode(HALF_WIDTH, -INITIAL_OVERLAP, 0.0)
upper_right = mesh.AddNode(
    HALF_WIDTH, BODY_HEIGHT - INITIAL_OVERLAP, 0.0
)
upper_left = mesh.AddNode(
    -HALF_WIDTH, BODY_HEIGHT - INITIAL_OVERLAP, 0.0
)
upper_body = mesh.AddFace(
    [slave_left, slave_right, upper_right, upper_left]
)

# Contact carriers are separate SMESH edge elements.  The exporter matches
# each pair of nodes to an exterior boundary of its material quadrangle.
master_edge = mesh.AddEdge([master_right, master_left])
slave_edge = mesh.AddEdge([slave_left, slave_right])

bodies = mesh.CreateEmptyGroup(SMESH.FACE, "BODIES")
bodies.Add([lower_body, upper_body])

contact_master = mesh.CreateEmptyGroup(SMESH.EDGE, "CONTACT_MASTER")
contact_master.Add([master_edge])

contact_slave = mesh.CreateEmptyGroup(SMESH.EDGE, "CONTACT_SLAVE")
contact_slave.Add([slave_edge])

fix_lower = mesh.CreateEmptyGroup(SMESH.NODE, "FIX_LOWER")
fix_lower.Add([lower_left, lower_right])

fix_upper = mesh.CreateEmptyGroup(SMESH.NODE, "FIX_UPPER")
fix_upper.Add([upper_left, upper_right])

smesh_builder.SetName(mesh.GetMesh(), "OOFEM_Contact_2D_Test")
salome.sg.updateObjBrowser()

print("Created OOFEM_Contact_2D_Test")
print("  8 nodes, 2 independent quadrangles, 2 contact edges")
print("  initial overlap: {}".format(INITIAL_OVERLAP))
print("  groups: BODIES, CONTACT_MASTER, CONTACT_SLAVE, FIX_LOWER, FIX_UPPER")
print("Suggested OOFEM plugin setup:")
print("  Quadrangle -> Quad1PlaneStrain")
print("  BODIES: Mooney-Rivlin compressible, d=0, C1=1000, C2=0,")
print("          K=10000, cross-section thickness=1, Element nlgeo=Enabled")
print("  FIX_LOWER: displacement DOF 1 = 0 and DOF 2 = 0")
print("  FIX_UPPER: displacement DOF 1 = 0 and DOF 2 = 0")
print("  Contact pair: master=CONTACT_MASTER, slave=CONTACT_SLAVE")
print("                pn=10000, pt=10000, friction=0, direct search,")
print("                one-pass, no orientation reversal")
print("  Solver preset: Penalty contact + VTK")
print("  nlgeo is enabled for Mooney-Rivlin, not required by contact itself")
print("  No external load is needed; the initial overlap activates contact")
