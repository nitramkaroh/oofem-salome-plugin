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


def load_export_variable_catalog():
    """Load the structured catalog of primary unknowns and internal state variables."""
    return _load_object("OOFEMExportVariables.json")


def solver_settings(preset_id=None, custom_record=None, variables=None):
    """Resolve an output-form entry (VTK on/off + record) by id or custom settings.

    The engineering model and its numeric solution controls live on the
    Analysis tab now; this only ever carries output-form (concern c) keys.
    """
    presets = load_solver_presets()
    selected = next(
        (preset for preset in presets if preset.get("id") == preset_id), presets[0]
    )

    if preset_id == "text-only":
        return {
            "vtk": False,
            "vtk_record": "",
        }

    # If explicit variables are provided, format them into the record
    if variables is not None:
        from OOFEMSalomePlugin.OOFEMExportCatalog import format_vtk_record

        record = format_vtk_record(variables)
        return {
            "vtk": bool(variables),
            "vtk_record": record,
        }

    # If explicit custom_record is provided
    if custom_record and isinstance(custom_record, str) and custom_record.strip():
        return {
            "vtk": True,
            "vtk_record": custom_record.strip(),
        }

    return {
        "vtk": bool(selected.get("vtk", False)),
        "vtk_record": selected.get(
            "vtk_record",
            "vtkxml tstep_all domain_all primvars 1 1 cellvars 1 1",
        ),
    }
