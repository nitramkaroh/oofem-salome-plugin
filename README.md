# OOFEM SALOME plugin

This plugin adds a dockable OOFEM workflow to the SALOME desktop. It turns
SMESH groups into a validated OOFEM model, runs OOFEM without freezing the
SALOME GUI, and opens native OOFEM VTK results in ParaVis.

## Features

- Explicit material and `SimpleCS` cross-section entities with references to
  structural edge, face, or volume groups
- Static structural, linear static, and eigenvalue-dynamic engineering models
  with editable analysis parameters
- Constant and piecewise-linear load-time functions shared by loads and
  prescribed conditions
- Multi-DOF displacements and nodal loads assigned to node groups
- Multi-component distributed loads assigned to boundary edge/face groups
- Complete OOFEM records for the supported linear structural elements
- Contiguous OOFEM ID remapping from arbitrary SALOME mesh IDs
- Pre-export validation of element order, assignments, references, overlaps,
  DOF arrays, and boundary-to-parent matching
- Atomic input generation: a failed writer never replaces a known-good input
- Non-blocking OOFEM execution with live output, timeout, cancel, and kill
  fallback
- Native .pvd/.vtu discovery, optional automatic ParaVis loading, and optional
  .vtu/.vtk to MED conversion through meshio
- Typed SALOME preferences for executable, working/results directories,
  timeout, and automatic postprocessing
- Editable material templates plus named steel, aluminium, and concrete presets
- Version-2 project JSON with deterministic legacy migration, isolated per
  SALOME study and embedded in HDF through native-module callbacks

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
4-node quadrangle family through a cross section's Element Mapping Override, as an
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
to persist them.

The default installer keeps the SALOME installation immutable. It installs the
module in an isolated OOFEM prefix, registers resources in the per-user
`SalomeApprc`, and adds the module through an `extra.env.d` hook used by the
generated launcher. Existing preference values are preserved. For older
standalone builds that cannot discover extension resources, the previous
global-resource workaround remains opt-in:

~~~bash
./install-salome-module.sh --salome /path/to/SALOME --legacy-global-registration
~~~

A CMake install path is also available for packaging:

~~~bash
cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/path/to/OOFEM-prefix
cmake --build build --target install
~~~

Open **File > Preferences > OOFEM** to configure the solver executable,
working and result directories, timeout, and automatic ParaVis loading.

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
4. Check the element mapping and choose the engineering model in **Analysis**.
5. Add materials. Creating a legacy-style material assignment also creates a
   compatible `SimpleCS`; review or edit it in **Cross Sections**.
6. Define constant or piecewise-linear histories in **Analysis**, then add
   boundary conditions with comma-separated DOFs/components and select their
   time function.
7. Configure the executable and run preferences in **File > Preferences >
   OOFEM**. Choose an input path in **Export / Solve** if desired.
8. Click **Validate**, then **Generate & Run**. A running job can be cancelled
   without freezing SALOME.
9. Click **Commit OOFEM Settings**, then save the SALOME study. Each open study
   retains an independent OOFEM project state.
10. In **Postprocess**, refresh results and open the .pvd in ParaVis (or enable
    automatic opening in preferences).

Values use the unit system chosen for the model; the plugin does not perform
unit conversion.

## How to test

### 1. Headless checks

These do not require SALOME or OOFEM:

~~~bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q salome_plugins.py src tests
~~~

Without an available solver, solver integration cases are reported
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
3. Add a **1D Truss** material on `bars`; set `E = 200` and `A = 2`.
   Review the generated `SimpleCS` assignment in **Cross Sections**.
4. Add one zero displacement on `fixed` with DOFs `1, 2, 3` and values
   `0, 0, 0`.
5. Add one zero displacement on `roller` with DOFs `2, 3` and values
   `0, 0`.
6. Add a nodal load on `loaded`, DOF `1`, component `10`.
7. Select **Linear Static** in **Analysis** and a VTK-enabled solver preset;
   select the OOFEM executable and an input path.
8. Click **Validate**. It should report 2 nodes, 1 element, 1 material,
   1 cross section, and 3 boundary conditions.
9. Click **Generate & Run**. The process must finish with exit code zero and
   OOFEM's `0 error(s)` summary.
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
2. Add a **2D Isotropic Elastic (Plane Stress)** material with
   `E=200000`, `nu=0.30`, `t=0.10` on `MAT_LEFT_FACES`.
3. Add another with `E=70000`, `nu=0.33`, `t=0.10` on
   `MAT_RIGHT_FACES`. Review the two generated `SimpleCS` records and
   their `thick=0.10` values.
4. Add one zero **Nodal Displacement** on `BC_FIXED_LEFT`, with DOFs
   `1, 2` and values `0, 0`.
5. Add **Surface Load (on Element Boundary)** on `LOAD_RIGHT_EDGE`, DOF
   `2`, component `-100`. As an alternative, use a **Nodal Load** on
   `LOAD_TOP_RIGHT_NODE`.
6. Validation should report 21 nodes, 12 elements, 2 materials, 2 cross
   sections, 2 boundary conditions, 4 sets, and a `2dplanestress` domain.

### 4. MED conversion

OOFEM writes VTK directly. MED conversion is optional and requires meshio in
the Python environment used by SALOME. After installing it there, select a
.vtu result and click **Convert VTK to MED…**.

## Result integration

The plugin activates ParaVis and uses SALOME's pvsimple API to open
.pvd, .vtu, .vtk, or .med data. A .pvd file is preferred because it retains
the complete time series. OOFEM's text .out remains available in the result
list for inspection but is not sent to ParaVis.

## Integration status and roadmap

The basic local structural workflow is integrated end to end: select a SMESH
mesh, define an analysis, materials, cross sections, time functions and
multi-component conditions, validate references, generate atomically, run or
cancel OOFEM, persist the project per study, and inspect native VTK output in
ParaVis.

The next implementation priorities are:

1. **Run history and reproducibility** — immutable timestamped run directories,
   a JSON run manifest (input hash, executable/version, command, exit state),
   rerun/duplicate actions, and explicit cleanup.
2. **Broader OOFEM records** — body/temperature loads, initial conditions,
   nonlinear solution controls, transient dynamics, and more cross-section,
   material, and element families. Each new record must have exporter and real
   solver tests.
3. **Result model** — expose fields and time steps in an OOFEM result tree,
   retain multiple runs, and improve MED export while keeping PVD/VTU the
   native path.
4. **SALOME interaction** — create/select SMESH groups from the OOFEM module,
   highlight invalid assignments in the 3D view, and provide a guided
   mesh-to-analysis wizard.
5. **Production execution** — local job queue, progress/state persistence,
   optional remote/HPC runner, provenance, and recovery after SALOME restarts.
6. **Release engineering** — one generated version source for CMake/Python/XML,
   hosted unit/install CI plus a serial self-hosted SALOME smoke test.

These priorities follow architectural patterns from
[AsterStudy](https://gitlab.com/salomemeca/modules/salome-asterstudy) without
copying its Code_Aster-specific catalog, remote-execution, or legacy packaging
layers.

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
