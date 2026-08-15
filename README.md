# OOFEM SALOME plugin

This plugin adds a dockable OOFEM workflow to the SALOME desktop. It turns
SMESH groups into a validated OOFEM model, runs OOFEM without freezing the
SALOME GUI, and opens native OOFEM VTK results in ParaVis.

## Features

- Material assignment per structural edge, face, or volume group
- Displacements and nodal loads assigned to node groups
- Distributed loads assigned to boundary edge/face groups
- Complete OOFEM records for the supported linear structural elements
- Contiguous OOFEM ID remapping from arbitrary SALOME mesh IDs
- Pre-export validation of element order, material overlap, group targets,
  degrees of freedom, and boundary-to-parent matching
- Export/Solve and Postprocess tabs
- Non-blocking OOFEM execution with live solver output
- Native .pvd/.vtu discovery and one-click ParaVis loading
- Optional .vtu/.vtk to MED conversion through meshio
- Linear-static VTK, text-only, and large-strain (nlgeo) solver presets
- Editable material templates plus named steel, aluminium, and concrete presets
- Versioned OOFEM project JSON embedded in the SALOME HDF study through native-module callbacks

The validated writer currently supports:

| SALOME cell | OOFEM element | Domain |
| --- | --- | --- |
| 2-node edge | Truss3D | 3D truss |
| 3-node triangle | TrPlaneStress2d | 2D plane stress |
| 4-node quadrangle | PlaneStress2d | 2D plane stress |
| 8-node quadrangle | QPlaneStrain (default), Quad1PlaneStrain/QPlaneStrain via override | 2D plane strain |
| 4-node tetrahedron | LTRSpace | 3D solid |
| 8-node hexahedron | LSpace | 3D solid |
| 20-node hexahedron | QSpace | 3D solid |

`Quad1PlaneStrain` (linear, 4-node plane strain) is also available for the
4-node quadrangle family through a material's Element Mapping Override, as an
alternative to the default `PlaneStress2d`.

Quadratic triangles, 9-node biquadratic quadrangles, 27-node hexahedra, and
other OOFEM element/material families are deliberately rejected until a
matching writer and solver test exist.

### Materials

Beyond the linear-elastic `ElasticIsotropic2d`/`ElasticIsotropic3d`/`Truss`
templates, the plugin also supports:

| Template | OOFEM keyword | Use case |
| --- | --- | --- |
| Isotropic Damage (idm1) | `idm1` | Quasi-brittle softening/cracking (e.g. concrete) |
| Von Mises Plasticity (MisesMat) | `misesmat` | Metal plasticity with linear isotropic hardening |
| Ogden Hyperelastic (compressible) | `ogdencompressiblemat` | Large-strain rubber-like materials (1-3 terms) |
| Mooney-Rivlin Hyperelastic (compressible) | `mooneyrivlincompressiblemat` | Large-strain rubber-like materials |

The Ogden and Mooney-Rivlin hyperelastic materials require large-displacement
kinematics. Select the **Large-strain static + VTK (nlgeo)** solver preset so
the plugin writes the `nlgeo 1` flag on every element record; without it,
OOFEM rejects these materials outright.

## Install as a SALOME module (recommended)

This is the AsterStudy-style integration. OOFEM appears in SALOME's module
selector alongside Geometry, Mesh, and ParaVis. Its activation callback loads
the SMESH engine automatically, so the Mesh module does not need to be opened
first.

~~~bash
./install-salome-module.sh \
  --salome /path/to/SALOME-9.16.0-native-UB24.04-SRC
~~~

Start the generated launcher:

~~~bash
/path/to/SALOME-9.16.0-native-UB24.04-SRC/salome-oofem
~~~

Select **OOFEM** from the module selector. The installer uses SALOME's
`SalomePyQtGUILight` Python-module bridge, a `SalomeApp.xml` resource file, and
the standard `initialize()`, `activate()`, `deactivate()`, `windows()`,
`views()`, `saveFiles()`, `openFiles()`, and `closeStudy()` lifecycle
callbacks. OOFEM settings are serialized to a versioned JSON document that SALOME
embeds in the HDF study. Click **Commit OOFEM Settings**, then use **File > Save**
to persist them. To support standalone native distributions whose GUI resource
manager is initialized before extension resource paths are added, the installer
also places one marked, idempotent OOFEM section in SALOME's main GUI
`SalomeApp.xml`. Its unmodified original is retained once as
`SalomeApp.xml.before-oofem`. Some standalone 9.16 builds do not expose an
extension's global section to the GUI resource manager. The installer and the
generated launcher therefore also register the same values idempotently in
`~/.config/salome/SalomeApprc.<version>` before SALOME starts. Existing user
preferences are preserved and a `.before-oofem` copy is made before the first
automatic modification.

The packaged wordmark is the official
[OOFEM logo](https://www.oofem.org/wiki/lib/exe/fetch.php?cache=&media=oofem-logo.png).
The full 400-by-46 image is retained as `module/oofem-logo.png`; the module and
fallback toolbar use a centered 48-by-48 icon derived from its first stylized
letter so it remains visible at SALOME toolbar size.

## Install as a Tools plugin (fallback)

The Tools plugin is retained for development and diagnostics. Use the native
module above for SALOME HDF save/open persistence and normal production work.


The installers use SALOME's per-user plugin directory, so administrator access
and changes to the SALOME installation are not required.

### Linux

~~~bash
git clone https://github.com/oofem/oofem-salome-plugin.git
cd oofem-salome-plugin
./install.sh
~~~

The default destination is:

~~~text
${XDG_CONFIG_HOME:-$HOME/.config}/salome/Plugins
~~~

A custom plugin directory can be supplied with:

~~~bash
./install.sh --target /path/to/salome/plugins
~~~

### Windows

~~~powershell
git clone https://github.com/oofem/oofem-salome-plugin.git
Set-Location oofem-salome-plugin
powershell -ExecutionPolicy Bypass -File .\install.ps1
~~~

The default destination is %USERPROFILE%\.config\salome\Plugins. A custom
location can be supplied with:

~~~powershell
.\install.ps1 -TargetDir C:\path\to\salome\plugins
~~~

Both plugin installers preserve an existing salome_plugins.py and add the
OOFEM registration block idempotently. Restart SALOME, then open
**Tools > Plugins > OOFEM**.

For development without installation, start SALOME with the checkout on its
plugin path:

~~~bash
export SALOME_PLUGINS_PATH="$PWD${SALOME_PLUGINS_PATH:+:$SALOME_PLUGINS_PATH}"
~~~

## Use

1. Create or import a SMESH mesh.
2. Create groups for each material region, constrained/loaded nodes, and any
   loaded boundary edges or faces.
3. Select the **OOFEM** module, or open **Tools > Plugins > OOFEM** when using
   the fallback plugin installation, and select the mesh.
4. Check the element mapping.
5. Add materials and assign each to a structural element group.
6. Add boundary conditions. The dialog filters node and boundary group choices
   according to the selected condition.
7. In **Export / Solve**, choose a preset, OOFEM executable, and .in path.
8. Click **Validate**, then **Generate & Run**.
9. Click **Commit OOFEM Settings**, then save the SALOME study to verify the JSON state is embedded.
10. In **Postprocess**, refresh the results and open the .pvd in ParaVis.

Values use the unit system chosen for the model; the plugin does not perform
unit conversion.

## How to test

### 1. Headless checks

These do not require SALOME or OOFEM:

~~~bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q salome_plugins.py src tests
~~~

Without an available solver, the four solver integration cases are reported
as skipped. Installer isolation, plugin registration, study persistence,
validation, result discovery, ParaVis dispatch, and MED conversion dispatch
still run.

`tests/test_main_widget.py` drives the actual `OOFEMMainWidget` under
`QT_QPA_PLATFORM=offscreen` (mesh/group discovery, material and boundary
condition property editing, validation, and study-state commit). It needs a
real PyQt5 or PySide2 install and reports itself skipped without one; every
other test file fakes Qt/SALOME away entirely.

### 2. Real OOFEM regression tests

Set OOFEM_BIN to the solver binary and run the full suite:

~~~bash
OOFEM_BIN=/absolute/path/to/oofem \
  python3 -m unittest discover -s tests -v
~~~

To run only the generated-input solves:

~~~bash
OOFEM_BIN=/absolute/path/to/oofem \
  python3 -m unittest \
  tests.test_exporter.OOFEMSolverIntegrationTests -v
~~~

The integration suite generates and solves:

- a two-node Truss3D, checking FL/EA = 0.025;
- a TrPlaneStress2d triangle with an edge-group load;
- a PlaneStress2d quadrangle with an edge-group load;
- an LTRSpace tetrahedron with a face-group load.

It also verifies that the VTK preset creates .vtu output.

### 3. SALOME end-to-end smoke test

Start SALOME with the installed plugin. In SALOME's Python console, run the
example using its absolute path:

~~~python
exec(open("/path/to/oofem-salome-plugin/examples/salome_truss_test.py").read())
~~~

Then:

1. Select the OOFEM module and click **Load/Refresh Data from Study**.
2. Select OOFEM_Truss_Test.
3. Add a **1D Truss** material on bars; set E = 200 and A = 2.
4. Add zero displacement BCs on fixed for DOFs 1, 2, and 3.
5. Add zero displacement BCs on roller for DOFs 2 and 3.
6. Add a nodal load on loaded, DOF 1, value 10.
7. Select **Linear static + VTK**, select the OOFEM executable and an input path.
8. Click **Validate**. It should report 2 nodes, 1 element, 1 material, and 6 BCs.
9. Click **Generate & Run**. The log should finish with 0 error(s).
10. Open the **Postprocess** tab and load the .pvd in ParaVis.

The resulting text output should give node 2, DOF 1 displacement 0.025.

For a more representative grouped 2D model, run:

~~~python
exec(open("/path/to/oofem-salome-plugin/examples/salome_plane_stress_groups_test.py").read())
~~~

This creates a 6-by-2 cantilever panel with 21 nodes, 12 quadrangles, two
non-overlapping face groups (`MAT_LEFT_FACES`, `MAT_RIGHT_FACES`), a fixed-left
node group, an optional point-load node group, and a right-edge boundary group.
Use the following plugin assignments:

1. Keep `Quadrangle -> PlaneStress2d` in **Element Mapping**.
2. Assign a **2D Isotropic Elastic (Plane Stress)** material with
   `E=200000`, `nu=0.30`, `t=0.10` to `MAT_LEFT_FACES`.
3. Assign another with `E=70000`, `nu=0.33`, `t=0.10` to
   `MAT_RIGHT_FACES`.
4. Add two zero **Nodal Displacement** conditions on `BC_FIXED_LEFT`, for
   DOFs 1 and 2.
5. Add **Surface Load (on Element Boundary)** on `LOAD_RIGHT_EDGE`, DOF 2,
   value `-100`. As an alternative load experiment, use a **Nodal Load** on
   `LOAD_TOP_RIGHT_NODE`.
6. Validation should report 21 nodes, 12 elements, 2 materials, 3 boundary
   conditions, 5 sets, and a `2dplanestress` domain.

### 4. MED conversion

OOFEM writes VTK directly. MED conversion is optional and requires meshio in
the Python environment used by SALOME. After installing it there, select a
.vtu result and click **Convert VTK to MED…**.

## Result integration

The plugin activates ParaVis and uses SALOME's pvsimple API to open
.pvd, .vtu, .vtk, or .med data. A .pvd file is preferred because it retains
the complete time series. OOFEM's text .out remains available in the result
list for inspection but is not sent to ParaVis.

## Development notes

SALOME 9.15 or newer is the current target. Qt is selected through
SalomePyQt, supporting the PyQt5 and PySide2 configurations used by SALOME 9.

The repository-level salome_plugins.py keeps imports lazy so SALOME can
discover the fallback menu action without loading SMESH, Qt widgets, or ParaVis
during startup. The native module never queries SALOMEDS for OOFEM state during
activation; its save/open callbacks own project persistence, while SMESH lookup
failures leave the dock open so Refresh can retry.

## License

GNU LGPL. See [LICENSE](LICENSE).
