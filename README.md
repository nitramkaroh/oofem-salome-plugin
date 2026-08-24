# OOFEM SALOME plugin

This plugin adds a dockable OOFEM workflow to the SALOME desktop. It turns
SMESH groups into a validated OOFEM model, runs OOFEM without freezing the
SALOME GUI, and opens native OOFEM VTK results in SALOME's integrated ParaView
(the module is technically named ParaVis).

## Features

- Explicit material and `SimpleCS` cross-section entities with references to
  structural edge, face, or volume groups
- Per-group continuum-element `nlgeo` control with inherit/on/off modes; a
  disjoint one-element SALOME assignment group provides individual-element
  control
- Static structural, linear static, and eigenvalue-dynamic engineering models
  with editable analysis parameters
- Constant and piecewise-linear load-time functions shared by loads and
  prescribed conditions
- Multi-DOF displacements and nodal loads assigned to node groups
- Multi-component distributed loads assigned to boundary edge/face groups
- Verified dead-weight/body loads and uniform structural temperature loads
- Constant structural initial displacement, velocity, and acceleration records
- Master/slave structural penalty contact generated from SALOME edge/face
  groups, including one-pass/two-pass enforcement and orientation control
- Complete OOFEM records for the supported linear structural elements
- Contiguous OOFEM ID remapping from arbitrary SALOME mesh IDs
- Pre-export validation of element order, assignments, references, overlaps,
  DOF arrays, and boundary-to-parent matching
- Atomic input generation: a failed writer never replaces a known-good input
- Non-blocking OOFEM execution with live output, timeout, cancel, and kill
  fallback
- Immutable per-study run history with versioned JSON manifests, input hashes,
  solver version/git provenance, logs, terminal states, rerun, and explicit
  cleanup
- Native .pvd/.vtu discovery with time-step and field summaries, optional
  automatic integrated ParaView loading, and optional .vtu/.vtk to MED
  conversion through meshio
- Typed SALOME preferences for executable, working/results directories,
  timeout, and automatic postprocessing
- Editable material templates plus named steel, aluminium, and concrete presets
- Version-3 project JSON with deterministic legacy migration, isolated per
  SALOME study and embedded in HDF through native-module callbacks

The validated writer currently supports:

| SALOME cell | OOFEM element | Domain |
| --- | --- | --- |
| 2-node edge | Truss3D | 3D truss |
| 3-node triangle | TrPlaneStress2d | 2D plane stress |
| 4-node quadrangle | PlaneStress2d (default), Quad1PlaneStrain via override | 2D plane stress/strain |
| 8-node quadrangle | QPlaneStrain | 2D plane strain |
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
kinematics. In the corresponding **Cross Section** assignment, set **Element
nlgeo** to **Enabled** so the plugin writes `nlgeo 1` on that group's continuum
element records. **Inherit solver preset** retains the convenient global
default of **Large-strain static + VTK (nlgeo)**, while an explicit **Disabled**
setting overrides it for the selected group.

Cross-section assignment groups must remain disjoint. To control one element
individually, move it out of the broader assignment group and create a
separate one-element group; overlapping assignments are rejected before
export.

### Structural contact

The **Contacts** tab creates OOFEM contact carrier elements directly from
SALOME boundary groups. The integration targets the current OOFEM contact
implementation and supports:

| SALOME boundary | OOFEM contact element | Domain | Integration points |
| --- | --- | --- | --- |
| Linear 2-node edge | `StructuralContactElement_LineLin` | Plane stress/plane strain | 2 |
| Linear 3-node face | `StructuralContactElement_TrLin` | 3D | 3 |
| Linear 4-node face | `StructuralContactElement_QuadLin` | 3D | 4 |

For every unique oriented boundary group, the exporter generates contact
elements, an element `Set`, and a `StructuralFEContactSurface`. It also writes
a shared `DummyCS`/`DummyMat`, `ncontactsurf`, and one or two
`structuralpenaltycontactbc` records. Contact-surface IDs deliberately match
the IDs of their element sets for compatibility with current OOFEM contact
initialization.

Adding the first contact automatically selects **Penalty contact + VTK**,
switches the engineering model to `StaticStructural`, and initializes ten
load steps. The preset requests displacement/stress plus contact gap,
pressure, and status fields 150-152. **Check Solver** shows the executable and
any repository URL, branch, and hash reported by the solver; the same
provenance is stored in each run manifest.

Contact enforcement is nonlinear and uses `StaticStructural`, but contact does
not require the continuum-element parameter `nlgeo 1`. The contact preset
therefore leaves newly inherited `nlgeo` off. If a model already inherits
`nlgeo` from the large-strain preset, adding contact materializes that state as
explicit **Enabled** before switching presets, so its continuum kinematics do
not change. Enable it independently on element groups whose
material/formulation needs finite-deformation kinematics; validation requires
it for Ogden and Mooney-Rivlin assignments. Carrier orientation is derived
from each owning continuum facet, independent of the standalone SMESH
edge/face order.
Use **Reverse master/slave orientation** only when the desired contact-search
normal is opposite to that outward parent-facet normal. In one-pass contact,
the slave side supplies the integration points; a finer/softer side is
normally the better slave. Two-pass adds the reverse condition and can
effectively increase the penalty response.

The production default is frictionless (`friction = 0`). Nonzero friction is
available in the current contact implementation, but should be introduced only
after the normal-penalty response is calibrated. The required
load-time-function reference is written, but current contact assembly does not
use it to ramp or deactivate contact. In 2D, contact integrates boundary
length rather than `SimpleCS` thickness, so forces are per unit thickness
unless the penalty is scaled consistently.

Quadratic contact boundaries, self-contact, rigid analytical surfaces,
mortar/augmented-Lagrange contact, axisymmetric contact, automatic penalty
selection, and the alternative `QuadLinGauss` carrier are not exported yet.

## Install as a SALOME module (recommended)

This is the AsterStudy-style integration. OOFEM appears in SALOME's module
selector alongside Geometry, Mesh, and ParaView (the SALOME module is
technically named ParaVis). Its activation callback loads the SMESH engine
automatically, so the Mesh module does not need to be opened first.

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
embeds in the HDF study. Every confirmed editor change updates the active study
and marks it modified automatically; use **File > Save** or **Ctrl+S** to
persist it. No plugin-specific commit step is required.

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
working and result directories, timeout, and automatic ParaView loading.

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
   time function. Body/dead-weight and uniform temperature loads target
   element groups.
7. Add optional zero-valued structural initial-condition records in **Initial
   Conditions**. Nonzero displacement, velocity, and acceleration values wait
   for a supported transient engineering model and are rejected for the
   currently available static/eigenvalue analyses.
8. For contact, create two exterior edge groups in 2D or face groups in 3D,
   then add a pair in **Contacts**. The plugin selects the nonlinear contact
   preset automatically; start frictionless and review master/slave direction.
9. Configure the executable and run preferences in **File > Preferences >
   OOFEM**. Choose an input path in **Export / Solve** if desired.
10. Click **Validate**, then **Generate & Run**. Each solve receives a new
   timestamped run directory and can be cancelled without freezing SALOME.
11. Save the SALOME study with **File > Save** or **Ctrl+S**. Each open study
    retains an independent OOFEM project state; no plugin-specific commit is
    needed.
12. In **Postprocess**, select any recorded run, inspect its status, time steps,
    and fields, then open the .pvd in ParaView. **Rerun as New** preserves the
    source run; deletion always requires confirmation. If SALOME was closed
    during a solve, use **Mark Interrupted** after confirming that the external
    OOFEM process has ended; partial results are retained and made read-only.

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
validation, result discovery, ParaView dispatch, and MED conversion dispatch
still run.

The MainWidget and dialog suites drive the actual Qt controls under
`QT_QPA_PLATFORM=offscreen` (mesh/group discovery, entity editing, validation,
and live study-state synchronization). They need a real PyQt5 or PySide2
install and report themselves skipped without one. SALOME services and SMESH
are faked in the headless suite.

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

Additional solver regressions cover dead weight against the analytical
uniform-body-force displacement, free thermal expansion, and acceptance of
the generated initial-condition record. Contact regressions target the current
OOFEM implementation and cover 2D line plus 3D triangular/quadrilateral carriers,
surface/set numbering, parent-facet orientation, one-pass and two-pass
conditions, contact VTK fields, and an exporter-generated continuum model.

To run only contact regressions against a selected binary:

~~~bash
OOFEM_BIN=/absolute/path/to/oofem \
  python3 -m unittest tests.test_contact_exporter -v
~~~

### 3. Automated real-SALOME acceptance

After installing the module, run the isolated terminal acceptance test against
the target SALOME and OOFEM binaries:

~~~bash
OOFEM_SALOME_ROOT=/absolute/path/to/SALOME-9.16.0-native-UB24.04-SRC \
OOFEM_BIN=/absolute/path/to/oofem \
  python3 -m unittest \
  tests.test_salome_acceptance.RealSalomeAcceptanceTests -v
~~~

This starts the generated `salome-oofem` launcher with a temporary user
configuration and checks native module discovery and callback-payload
roundtrip, a separate SALOMEDS AttributeString HDF save/reopen, a real SMESH
contact model, contact export with element `nlgeo` disabled, execution by the
selected OOFEM binary, real PVD output, and loading that output through
SALOME's integrated ParaView `PVDReader`. GUI module/dock activation and
SALOME's automatic embedding of the callback payload remain part of the
manual smoke test below.

### 4. Manual SALOME GUI smoke test

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
10. Use **File > Save As**, close the study, reopen its HDF file, and select
    OOFEM again. The mesh selection, model definitions, and run history must
    be restored without a plugin-specific commit action.
11. Open the **Postprocess** tab and load the .pvd in ParaView.

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

For a minimal two-body contact setup, run:

~~~python
exec(open("/path/to/oofem-salome-plugin/examples/salome_contact_2d_test.py").read())
~~~

Assign `Quad1PlaneStrain` to the quadrangles, use a compressible
Mooney-Rivlin material on `BODIES`, set that cross-section assignment's
**Element nlgeo** to **Enabled**, constrain `FIX_LOWER` and `FIX_UPPER`, and
create a frictionless pair from `CONTACT_MASTER` to `CONTACT_SLAVE`. Adding the
pair selects and locks the current-OOFEM contact preset automatically; no
solver implementation/profile choice is exposed. For a small-strain elastic
contact model, leave **Element nlgeo** disabled or inherited.

### 5. MED conversion

OOFEM writes VTK directly. MED conversion is optional and requires meshio in
the Python environment used by SALOME. After installing it there, select a
.vtu result and click **Convert VTK to MED…**.

## Result integration

The plugin activates SALOME's ParaVis module, which embeds ParaView, and uses
its `pvsimple` API to open
.pvd, .vtu, .vtk, or .med data. A .pvd file is preferred because it retains
the complete time series. OOFEM's text .out remains available in the result
list for inspection but is not sent to ParaView.

Each **Generate & Run** operation creates:

~~~text
<results>/oofem-runs/<project-id>/<run-id>/
  run.json
  solver.log
  input/<model>.in
  results/<model>.out[.m0.pvd/.vtu/...]
~~~

`run.json` contains the project snapshot, input SHA-256, exact solver
command and detected `oofem -v` provenance, timestamps, exit state, and
result metadata. Result hashing uses a 64 MiB total synchronous budget; larger
artifacts retain size and modification-time provenance without freezing the
SALOME GUI. Registered inputs, logs, result files, and result directories are
made read-only. Rerunning always creates a new run and records its
`source_run_id`. MED conversion is deliberately saved outside this immutable
archive.

## Integration status and roadmap

The basic local structural workflow is integrated end to end: select a SMESH
mesh, define an analysis, materials, cross sections, time functions and
multi-component conditions, validate references, generate atomically, run or
cancel OOFEM, persist the project per study, and inspect native VTK output in
ParaView.

Run history/provenance, multiple retained runs, PVD time-step/field summaries,
dead weight, uniform temperature loads, and constant structural initial
conditions are now implemented. Current-OOFEM 2D/3D penalty contact is also
implemented with automatic solver setup. The next priorities are:

1. **Nonlinear and transient analyses** — nonlinear solution controls,
   `DIIDynamic`/transient dynamics, physically exercised velocity and
   acceleration initial conditions, and restart/checkpoint support.
2. **Broader OOFEM records** — thermal gradients for beam/plate/shell
   families, more cross sections, materials, elements, coupled-field records,
   and capability-gated advanced contact/search/result options. Each addition
   must retain exporter and real-solver tests.
3. **Result tree and comparisons** — a hierarchical fields/time-step browser,
   side-by-side run comparison, plots/probes, and improved MED export while
   keeping PVD/VTU native.
4. **SALOME interaction** — create/select SMESH groups from the OOFEM module,
   highlight invalid assignments in the 3D view, and provide a guided
   mesh-to-analysis wizard.
5. **Production execution** — local job queue, progress/state persistence,
   optional remote/HPC runner, and automatic detection of externally finished
   jobs after SALOME restarts. Manual stale-run recovery is already available
   through **Mark Interrupted**.
6. **Release engineering** — one generated version source for CMake/Python/XML
   and hosted unit/install CI. The serial real-SALOME terminal acceptance is
   implemented; automated GUI activation and packaging remain.

These priorities follow architectural patterns from
[AsterStudy](https://gitlab.com/salomemeca/modules/salome-asterstudy) without
copying its Code_Aster-specific catalog, remote-execution, or legacy packaging
layers.

## Development notes

SALOME 9.15 or newer is the current target. Qt is selected through
SalomePyQt, supporting the PyQt5 and PySide2 configurations used by SALOME 9.

The repository-level salome_plugins.py keeps imports lazy so SALOME can
discover the fallback menu action without loading SMESH, Qt widgets, or ParaView
during startup. The native module never queries SALOMEDS for OOFEM state during
activation; its save/open callbacks own project persistence, while SMESH lookup
failures leave the dock open so Refresh can retry.

## License

GNU LGPL. See [LICENSE](LICENSE).
