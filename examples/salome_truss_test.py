"""Create the two-node truss used by the plugin's OOFEM integration test.

Run this file in SALOME's Python console (File > Load Script), then open
the OOFEM module selector entry (or Tools > Plugins > OOFEM in fallback mode).
"""

import salome

salome.salome_init()

import SMESH
from salome.smesh import smeshBuilder


smesh_builder = smeshBuilder.New()
mesh = smesh_builder.Mesh()

node_1 = mesh.AddNode(0.0, 0.0, 0.0)
node_2 = mesh.AddNode(1.0, 0.0, 0.0)
bar = mesh.AddEdge([node_1, node_2])

bars = mesh.CreateEmptyGroup(SMESH.EDGE, "bars")
bars.Add([bar])

fixed = mesh.CreateEmptyGroup(SMESH.NODE, "fixed")
fixed.Add([node_1])

roller = mesh.CreateEmptyGroup(SMESH.NODE, "roller")
roller.Add([node_2])

loaded = mesh.CreateEmptyGroup(SMESH.NODE, "loaded")
loaded.Add([node_2])

smesh_builder.SetName(mesh.GetMesh(), "OOFEM_Truss_Test")
salome.sg.updateObjBrowser()

print("Created OOFEM_Truss_Test with groups: bars, fixed, roller, loaded")
print("Use E=200, A=2 and Fx=10; expected node-2 ux is 0.025.")
