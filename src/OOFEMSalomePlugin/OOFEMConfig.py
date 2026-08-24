"""Data-driven configuration loaders for the OOFEM plugin."""

import json
import os


PLUGIN_DIRECTORY = os.path.dirname(os.path.abspath(__file__))


def _load_object(filename):
    path = os.path.join(PLUGIN_DIRECTORY, filename)
    with open(path, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("{} must contain a JSON object".format(filename))
    return value


def load_material_catalog():
    data = _load_object("OOFEMMaterials.json")
    return data.get("materials", []), data.get("library", [])


def load_boundary_condition_templates():
    return _load_object("OOFEMBCs.json").get("boundary_conditions", [])


def load_initial_condition_templates():
    return _load_object("OOFEMBCs.json").get("initial_conditions", [])


def load_analysis_templates():
    return _load_object("OOFEMAnalyses.json").get("analyses", [])


def load_cross_section_templates():
    return _load_object("OOFEMCrossSections.json").get("cross_sections", [])


def load_time_function_templates():
    return _load_object("OOFEMTimeFunctions.json").get("time_functions", [])


def load_solver_presets():
    presets = _load_object("OOFEMSolverPresets.json").get("solver_presets", [])
    if not presets:
        raise ValueError("OOFEMSolverPresets.json contains no solver presets")
    return presets


def solver_settings(preset_id=None):
    presets = load_solver_presets()
    selected = next(
        (preset for preset in presets if preset.get("id") == preset_id), presets[0]
    )
    settings = {
        "engng_model": selected.get("engng_model", "StaticStructural"),
        "nsteps": int(selected.get("nsteps", 1)),
        "vtk": bool(selected.get("vtk", False)),
        "vtk_record": selected.get(
            "vtk_record",
            "vtkxml tstep_all domain_all primvars 1 1 cellvars 1 1",
        ),
        "nlgeom": bool(selected.get("nlgeom", False)),
    }
    for key in (
        "rtolv",
        "maxiter",
        "manrmsteps",
        "initialguess",
        "smtype",
        "stiffmode",
        "renumber",
    ):
        if selected.get(key) is not None:
            settings[key] = selected[key]
    return settings
