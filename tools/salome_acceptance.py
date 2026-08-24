#!/usr/bin/env python3
"""Acceptance checks for the OOFEM module inside a real SALOME runtime.

Run the automated terminal slice with::

    /path/to/salome-oofem start -t -w 1 --nosave-config \
        /path/to/oofem-salome-plugin/tools/salome_acceptance.py

Set ``OOFEM_ACCEPTANCE_SOURCE_ROOT`` to a checkout to test its Python code
without installing it.  Module discovery is still checked against the OOFEM
prefix registered by the real SALOME launcher.  Execute this file from the
SALOME Python console for the additional GUI activation/dock check, or set
``OOFEM_ACCEPTANCE_REQUIRE_GUI=1`` to turn its absence into a failure.

The script intentionally uses only temporary result and callback files.  It
does not install the module, edit SALOME resources, save a user study, or stop
an existing SALOME session.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET


RESULT_PREFIX = "OOFEM_SALOME_ACCEPTANCE_RESULT="
EXPECTED_CALLBACKS = (
    "initialize",
    "activate",
    "deactivate",
    "windows",
    "views",
    "createPreferences",
    "saveFiles",
    "openFiles",
    "closeStudy",
)


def _truthy(value):
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def split_modules(value):
    """Return normalized module names from SALOME's comma/colon syntax."""
    result = []
    seen = set()
    for item in re.split(r"[,;:]", value or ""):
        item = item.strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


class AcceptanceReport:
    def __init__(self):
        self.checks = []
        self.warnings = []
        self.runtime = {}

    def pass_check(self, name, detail=None):
        self.checks.append({"name": name, "status": "pass", "detail": detail})

    def fail_check(self, name, error):
        self.checks.append(
            {"name": name, "status": "fail", "detail": str(error)}
        )

    def skip_check(self, name, detail):
        self.checks.append({"name": name, "status": "skip", "detail": detail})

    def warn(self, message):
        self.warnings.append(str(message))

    @property
    def ok(self):
        return all(item["status"] != "fail" for item in self.checks)

    def run(self, name, callback):
        try:
            detail = callback()
        except Exception as error:  # acceptance boundary: preserve all evidence
            self.fail_check(name, "{}: {}".format(type(error).__name__, error))
            traceback.print_exc()
        else:
            self.pass_check(name, detail)

    def as_dict(self):
        return {
            "ok": self.ok,
            "checks": self.checks,
            "warnings": self.warnings,
            "runtime": self.runtime,
        }


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def _salome_version():
    try:
        from salome.salome_version import getVersion
    except ImportError:
        from salome_version import getVersion
    return getVersion("KERNEL")


def _find_module_parameter(root, section_name, parameter_name):
    section = root.find(".//section[@name='{}']".format(section_name))
    if section is None:
        return None
    parameter = section.find("parameter[@name='{}']".format(parameter_name))
    return parameter.get("value") if parameter is not None else None


def check_runtime_discovery(report):
    version = _salome_version()
    expected_version = os.environ.get("OOFEM_ACCEPTANCE_EXPECT_SALOME", "9.16")
    _require(
        version.startswith(expected_version),
        "SALOME {} was requested, runtime is {}".format(expected_version, version),
    )

    modules = split_modules(os.environ.get("SALOME_MODULES", ""))
    module_keys = {item.casefold() for item in modules}
    missing = {
        required
        for required in ("OOFEM", "SMESH", "PARAVIS")
        if required.casefold() not in module_keys
    }
    _require(not missing, "SALOME_MODULES misses {}".format(sorted(missing)))

    raw_module_root = os.environ.get("OOFEM_ROOT_DIR", "")
    _require(raw_module_root, "OOFEM_ROOT_DIR is not set")
    module_root = Path(raw_module_root).resolve()
    callback = module_root / "bin" / "salome" / "OOFEMGUI.py"
    resource_root = module_root / "share" / "salome" / "resources" / "oofem"
    resource_file = resource_root / "SalomeApp.xml"
    _require(callback.is_file(), "installed OOFEMGUI.py is missing")
    _require(resource_file.is_file(), "installed OOFEM SalomeApp.xml is missing")

    document = ET.parse(str(resource_file)).getroot()
    library = _find_module_parameter(document, "OOFEM", "library")
    _require(
        library == "SalomePyQtGUILight",
        "OOFEM module library is {!r}, expected SalomePyQtGUILight".format(
            library
        ),
    )

    app_config = [
        Path(item).resolve()
        for item in os.environ.get("SalomeAppConfig", "").split(":")
        if item
    ]
    _require(
        resource_root in app_config,
        "OOFEM resource root is absent from SalomeAppConfig",
    )

    spec = importlib.util.find_spec("OOFEMGUI")
    _require(spec is not None and spec.origin, "OOFEMGUI is not discoverable")
    discovered_callback = Path(spec.origin).resolve()
    _require(
        discovered_callback == callback,
        "OOFEMGUI resolves to {}, expected {}".format(
            discovered_callback, callback
        ),
    )

    report.runtime.update(
        {
            "salome_version": version,
            "salome_modules": modules,
            "oofem_root": str(module_root),
            "discovered_callback": str(discovered_callback),
        }
    )
    return {
        "version": version,
        "module_root": str(module_root),
        "callback": str(discovered_callback),
        "resource": str(resource_file),
    }


def _source_override():
    raw_source = os.environ.get("OOFEM_ACCEPTANCE_SOURCE_ROOT", "").strip()
    if not raw_source:
        return None
    source_root = Path(raw_source).resolve()
    module_path = source_root / "module"
    package_path = source_root / "src"
    _require(
        (module_path / "OOFEMGUI.py").is_file(),
        "source override has no module/OOFEMGUI.py",
    )
    _require(
        (package_path / "OOFEMSalomePlugin" / "__init__.py").is_file(),
        "source override has no src/OOFEMSalomePlugin package",
    )
    for path in (str(package_path), str(module_path)):
        while path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
    return source_root


def check_callback_surface(report):
    source_root = _source_override()
    import OOFEMGUI

    missing = [name for name in EXPECTED_CALLBACKS if not callable(getattr(OOFEMGUI, name, None))]
    _require(not missing, "OOFEMGUI misses callbacks {}".format(missing))

    callback_file = Path(OOFEMGUI.__file__).resolve()
    if source_root is not None:
        expected = (source_root / "module" / "OOFEMGUI.py").resolve()
        _require(
            callback_file == expected,
            "callback under test is {}, expected checkout {}".format(
                callback_file, expected
            ),
        )
        report.runtime["source_override"] = str(source_root)
        _report_installation_drift(report, source_root)
    report.runtime["callback_under_test"] = str(callback_file)
    return {"callbacks": list(EXPECTED_CALLBACKS), "file": str(callback_file)}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report_installation_drift(report, source_root):
    raw_installed = os.environ.get("OOFEM_ROOT_DIR", "")
    if not raw_installed:
        return
    installed = Path(raw_installed).resolve() / "bin" / "salome"
    pairs = (
        (source_root / "module" / "OOFEMGUI.py", installed / "OOFEMGUI.py"),
        (
            source_root / "module" / "oofem_preferences.py",
            installed / "oofem_preferences.py",
        ),
        (
            source_root / "module" / "register_oofem_user_config.py",
            installed / "register_oofem_user_config.py",
        ),
        (
            source_root / "src" / "OOFEMSalomePlugin" / "OOFEMModule.py",
            installed / "OOFEMSalomePlugin" / "OOFEMModule.py",
        ),
        (
            source_root
            / "src"
            / "OOFEMSalomePlugin"
            / "OOFEMMainWidget.py",
            installed / "OOFEMSalomePlugin" / "OOFEMMainWidget.py",
        ),
        (
            source_root / "src" / "OOFEMSalomePlugin" / "OOFEMPost.py",
            installed / "OOFEMSalomePlugin" / "OOFEMPost.py",
        ),
    )
    drift = []
    for source, target in pairs:
        if not target.is_file() or _sha256(source) != _sha256(target):
            drift.append(str(target))
    if not drift:
        return
    message = "installed OOFEM copy differs from checkout: {}".format(
        ", ".join(drift)
    )
    if _truthy(os.environ.get("OOFEM_ACCEPTANCE_REQUIRE_INSTALLED_SYNC")):
        raise AssertionError(message)
    report.warn(message)


def check_native_callbacks():
    import salome
    import OOFEMGUI
    from OOFEMSalomePlugin.OOFEMModule import getModule
    from OOFEMSalomePlugin.OOFEMProject import new_project_state
    from OOFEMSalomePlugin.OOFEMState import OOFEMState, STATE_FILE_NAME

    salome.salome_init()
    study = getattr(salome, "myStudy", None)
    _require(study is not None, "SALOME did not provide an active study")

    module = getModule()
    module._select_study(study, snapshot=False)
    state = new_project_state()
    state["acceptance_marker"] = "native-callback-roundtrip"
    _require(module.set_study_state(state), "module rejected acceptance state")

    with tempfile.TemporaryDirectory(prefix="oofem-salome-callback-") as directory:
        names = OOFEMGUI.saveFiles(directory, "acceptance.hdf")
        _require(
            names == [STATE_FILE_NAME],
            "saveFiles returned {!r}".format(names),
        )
        payload = Path(directory) / STATE_FILE_NAME
        _require(payload.is_file(), "saveFiles payload was not created")
        saved = OOFEMState.load_file(str(payload))
        _require(
            saved and saved.get("acceptance_marker") == state["acceptance_marker"],
            "saved callback payload lost the marker",
        )

        replacement = new_project_state()
        _require(module.set_study_state(replacement), "could not replace state")
        opened = OOFEMGUI.openFiles(
            [directory, STATE_FILE_NAME], "acceptance.hdf"
        )
        _require(opened is True, "openFiles rejected its own payload")
        _require(
            module.study_state.get("acceptance_marker")
            == state["acceptance_marker"],
            "openFiles did not restore the marker",
        )

    return {
        "payload": STATE_FILE_NAME,
        "marker": module.study_state.get("acceptance_marker"),
        "url": module.study_url,
    }


def check_salomeds_attribute_hdf_roundtrip():
    import salome
    from OOFEMSalomePlugin.OOFEMState import OOFEMState

    salome.salome_init()
    study = getattr(salome, "myStudy", None)
    _require(study is not None, "SALOME did not provide an active study")
    state = {
        "schema_version": 3,
        "acceptance_marker": "salomeds-hdf-roundtrip",
        "selected_mesh_id": "0:1:2",
        "cross_sections": [
            {
                "id": "cs-1",
                "element_options": {"nlgeo": "off"},
            }
        ],
        "contacts": [
            {
                "id": "contact-1",
                "master_group": "MASTER",
                "slave_group": "SLAVE",
            }
        ],
    }
    with tempfile.TemporaryDirectory(prefix="oofem-salome-hdf-") as directory:
        hdf = Path(directory) / "acceptance.hdf"
        _require(OOFEMState.save(study, state), "AttributeString save failed")
        _require(study.SaveAs(str(hdf), 0, 0), "SALOME Study.SaveAs failed")
        _require(hdf.is_file(), "SALOME created no HDF study")
        hdf_size = hdf.stat().st_size

        salome.salome_close()
        salome.salome_init(str(hdf), forced=True)
        reopened = getattr(salome, "myStudy", None)
        _require(reopened is not None, "SALOME did not reopen the HDF study")
        loaded = OOFEMState.load(reopened)
        _require(loaded == state, "HDF reopen returned {!r}".format(loaded))
        return {
            "bytes": hdf_size,
            "marker": loaded["acceptance_marker"],
            "nlgeo": loaded["cross_sections"][0]["element_options"]["nlgeo"],
            "contacts": len(loaded["contacts"]),
        }


def check_smesh_oofem_paraview():
    """Exercise the production mesh/export/solve/result path in SALOME."""
    import salome
    import SMESH
    from salome.smesh import smeshBuilder

    from OOFEMSalomePlugin.OOFEMConfig import (
        load_boundary_condition_templates,
        solver_settings,
    )
    from OOFEMSalomePlugin.OOFEMExporter import OOFEMExporter
    from OOFEMSalomePlugin.OOFEMPost import (
        discover_result_files,
        open_in_paravis,
        preferred_visualization_file,
    )

    raw_binary = os.environ.get("OOFEM_ACCEPTANCE_OOFEM_BIN", "").strip()
    _require(raw_binary, "OOFEM_ACCEPTANCE_OOFEM_BIN is not set")
    binary = Path(raw_binary).expanduser().resolve()
    _require(binary.is_file(), "OOFEM executable is missing: {}".format(binary))
    _require(os.access(str(binary), os.X_OK), "OOFEM is not executable: {}".format(binary))

    salome.salome_init()
    smesh = smeshBuilder.New()
    mesh = smesh.Mesh()

    lower_left = mesh.AddNode(-0.5, -1.0, 0.0)
    lower_right = mesh.AddNode(0.5, -1.0, 0.0)
    master_right = mesh.AddNode(0.5, 0.0, 0.0)
    master_left = mesh.AddNode(-0.5, 0.0, 0.0)
    lower_body = mesh.AddFace(
        [lower_left, lower_right, master_right, master_left]
    )

    slave_left = mesh.AddNode(-0.5, -0.01, 0.0)
    slave_right = mesh.AddNode(0.5, -0.01, 0.0)
    upper_right = mesh.AddNode(0.5, 0.99, 0.0)
    upper_left = mesh.AddNode(-0.5, 0.99, 0.0)
    upper_body = mesh.AddFace(
        [slave_left, slave_right, upper_right, upper_left]
    )

    master_edge = mesh.AddEdge([master_right, master_left])
    slave_edge = mesh.AddEdge([slave_left, slave_right])

    bodies = mesh.CreateEmptyGroup(SMESH.FACE, "BODIES")
    bodies.Add([lower_body, upper_body])
    master = mesh.CreateEmptyGroup(SMESH.EDGE, "CONTACT_MASTER")
    master.Add([master_edge])
    slave = mesh.CreateEmptyGroup(SMESH.EDGE, "CONTACT_SLAVE")
    slave.Add([slave_edge])
    fixed_lower = mesh.CreateEmptyGroup(SMESH.NODE, "FIX_LOWER")
    fixed_lower.Add([lower_left, lower_right])
    fixed_upper = mesh.CreateEmptyGroup(SMESH.NODE, "FIX_UPPER")
    fixed_upper.Add([upper_left, upper_right])
    smesh.SetName(mesh.GetMesh(), "OOFEM_Acceptance_Contact")

    material = {
        "id": "material-1",
        "name": "small-strain contact bodies",
        "oofem_type": "ElasticIsotropic2d",
        "params": {"E": 1000.0, "nu": 0.25, "t": 1.0},
    }
    cross_section = {
        "id": "cross-section-1",
        "name": "contact body section",
        "oofem_type": "SimpleCS",
        "material_id": "material-1",
        "assigned_group": "BODIES",
        "element_options": {"nlgeo": "off"},
        "params": {"thick": 1.0},
    }
    boundary_conditions = [
        {
            "id": "bc-lower",
            "name": "fix lower body",
            "oofem_type": "Displacement",
            "assigned_group": "FIX_LOWER",
            "params": {"dofs": [1, 2], "values": [0.0, 0.0]},
        },
        {
            "id": "bc-upper",
            "name": "fix upper body",
            "oofem_type": "Displacement",
            "assigned_group": "FIX_UPPER",
            "params": {"dofs": [1, 2], "values": [0.0, 0.0]},
        },
    ]
    contact = {
        "id": "contact-1",
        "name": "body interface",
        "oofem_type": "StructuralPenaltyContactBC",
        "master_group": "CONTACT_MASTER",
        "slave_group": "CONTACT_SLAVE",
        "time_function_id": "ltf-1",
        "params": {
            "normal_penalty": 1.0e4,
            "tangential_penalty": 1.0e4,
            "friction": 0.0,
            "algorithm": 0,
            "two_pass": False,
            "reverse_master": False,
            "reverse_slave": False,
        },
    }
    settings = solver_settings("contact-static-vtk")
    settings["nsteps"] = 1

    with tempfile.TemporaryDirectory(prefix="oofem-salome-pipeline-") as directory:
        input_path = Path(directory) / "contact.in"
        exporter = OOFEMExporter(
            mesh.GetMesh(),
            {"Quadrangle": "Quad1PlaneStrain"},
            [material],
            boundary_conditions,
            load_boundary_condition_templates(),
            solver_settings=settings,
            cross_sections=[cross_section],
            contacts=[contact],
        )
        summary = exporter.validate()
        _require(
            summary["contact_elements"] > 0,
            "exporter validated no contact carrier elements",
        )
        exporter.export(str(input_path))
        input_text = input_path.read_text(encoding="utf-8")
        input_tokens = re.findall(r"\S+", input_text.casefold())
        _require("nlgeo" not in input_tokens, "contact unexpectedly forced nlgeo")

        completed = subprocess.run(
            [str(binary), "-f", str(input_path)],
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        _require(
            completed.returncode == 0,
            "OOFEM exited {}:\n{}".format(completed.returncode, completed.stdout),
        )
        _require("0 error(s)" in completed.stdout, completed.stdout)

        results = discover_result_files(str(input_path))
        pvd = preferred_visualization_file(results)
        _require(pvd and pvd.endswith(".pvd"), "OOFEM created no PVD result")
        source = open_in_paravis(pvd)
        _require(source is not None, "ParaView returned no reader for OOFEM PVD")
        reader = source.GetXMLName()
        _require(reader == "PVDReader", "unexpected ParaView reader: {}".format(reader))
        import pvsimple

        pvsimple.Delete(source)
        return {
            "nodes": summary["nodes"],
            "elements": summary["elements"],
            "contact_elements": summary["contact_elements"],
            "nlgeo": False,
            "solver": str(binary),
            "reader": reader,
        }


def write_pvd_fixture(directory):
    """Write a tiny OOFEM-like time collection and return its PVD path."""
    directory = Path(directory)
    vtu = directory / "oofem-step-1.vtu"
    pvd = directory / "oofem-result.pvd"
    vtu.write_text(
        """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="4" NumberOfCells="1">
      <PointData Scalars="temperature">
        <DataArray type="Float64" Name="temperature" format="ascii">0 1 2 3</DataArray>
      </PointData>
      <CellData/>
      <Points>
        <DataArray type="Float64" NumberOfComponents="3" format="ascii">
          0 0 0  1 0 0  0 1 0  0 0 1
        </DataArray>
      </Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 3</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">4</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">10</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
""",
        encoding="utf-8",
    )
    pvd.write_text(
        """<?xml version="1.0"?>
<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">
  <Collection>
    <DataSet timestep="1" group="" part="0" file="oofem-step-1.vtu"/>
  </Collection>
</VTKFile>
""",
        encoding="utf-8",
    )
    return pvd


def check_paravis():
    from OOFEMSalomePlugin.OOFEMPost import open_in_paravis

    with tempfile.TemporaryDirectory(prefix="oofem-salome-paravis-") as directory:
        pvd = write_pvd_fixture(directory)
        source = open_in_paravis(str(pvd))
        _require(source is not None, "open_in_paravis returned no source")
        reader = source.GetXMLName()
        _require(
            reader == "PVDReader",
            "ParaVis selected reader {!r}, expected PVDReader".format(reader),
        )
        import pvsimple

        pvsimple_path = Path(pvsimple.__file__).resolve()
        _require(
            "PARAVIS" in str(pvsimple_path).upper(),
            "pvsimple did not come from SALOME ParaVis: {}".format(pvsimple_path),
        )
        pvsimple.Delete(source)
        return {"reader": reader, "pvsimple": str(pvsimple_path)}


def _inside_salome_gui():
    try:
        import salome_iapp
    except ImportError:
        return False
    return bool(getattr(salome_iapp, "IN_SALOME_GUI", False))


def check_gui_activation():
    import SalomePyQt
    from OOFEMSalomePlugin.OOFEMModule import getModule

    gui = SalomePyQt.SalomePyQt()
    activated = gui.activateModule("OOFEM")
    gui.processEvents()
    _require(bool(activated), "SalomePyQt.activateModule('OOFEM') returned false")
    active = str(gui.getActivePythonModule())
    _require("oofem" in active.casefold(), "active Python module is {!r}".format(active))
    module = getModule()
    _require(module.dock is not None, "OOFEM activation created no dock")
    _require(module.dock.isVisible(), "OOFEM dock is not visible after activation")
    return {"active_module": active, "dock": module.dock.objectName()}


def run_acceptance():
    report = AcceptanceReport()
    report.run("runtime_discovery", lambda: check_runtime_discovery(report))
    if report.ok:
        report.run("callback_surface", lambda: check_callback_surface(report))
    if report.ok:
        report.run("native_callback_payload_roundtrip", check_native_callbacks)
        report.run(
            "salomeds_attribute_hdf_roundtrip",
            check_salomeds_attribute_hdf_roundtrip,
        )

    if _truthy(os.environ.get("OOFEM_ACCEPTANCE_SKIP_PIPELINE")):
        report.skip_check(
            "smesh_oofem_paraview",
            "OOFEM_ACCEPTANCE_SKIP_PIPELINE is set",
        )
    elif os.environ.get("OOFEM_ACCEPTANCE_OOFEM_BIN", "").strip():
        if report.ok:
            report.run("smesh_oofem_paraview", check_smesh_oofem_paraview)
    elif _truthy(os.environ.get("OOFEM_ACCEPTANCE_REQUIRE_PIPELINE")):
        report.fail_check(
            "smesh_oofem_paraview",
            "OOFEM_ACCEPTANCE_REQUIRE_PIPELINE is set, but "
            "OOFEM_ACCEPTANCE_OOFEM_BIN is empty",
        )
    else:
        report.skip_check(
            "smesh_oofem_paraview",
            "set OOFEM_ACCEPTANCE_OOFEM_BIN to run the real solver pipeline",
        )

    if _truthy(os.environ.get("OOFEM_ACCEPTANCE_SKIP_PARAVIS")):
        report.skip_check("paravis_pvd", "OOFEM_ACCEPTANCE_SKIP_PARAVIS is set")
    elif report.ok:
        report.run("paravis_pvd", check_paravis)

    if _inside_salome_gui():
        report.run("gui_module_activation", check_gui_activation)
    elif _truthy(os.environ.get("OOFEM_ACCEPTANCE_REQUIRE_GUI")):
        report.fail_check(
            "gui_module_activation",
            "OOFEM_ACCEPTANCE_REQUIRE_GUI is set, but no SALOME GUI is active",
        )
    else:
        report.skip_check(
            "gui_module_activation",
            "terminal acceptance; execute the script in SALOME's Python console",
        )
    return report


def main():
    report = run_acceptance()
    payload = json.dumps(report.as_dict(), sort_keys=True)
    print(RESULT_PREFIX + payload, flush=True)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
