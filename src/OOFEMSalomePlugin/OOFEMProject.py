"""Versioned, JSON-serializable OOFEM project data.

This module deliberately has no Qt or SALOME dependencies.  It defines the
canonical project shape independently of study storage and provides a pure
migration function for the flat state dictionaries used by the original GUI.
"""

from copy import deepcopy


PROJECT_SCHEMA_VERSION = 2

_ANALYSIS_ID = "analysis-1"
_TIME_FUNCTION_ID = "ltf-1"


def new_project_state():
    """Return a fresh, canonical version-2 project dictionary."""
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "analysis": {
            "id": _ANALYSIS_ID,
            "oofem_type": "StaticStructural",
            "params": {"nsteps": 1},
        },
        "time_functions": [
            {
                "id": _TIME_FUNCTION_ID,
                "oofem_type": "ConstantFunction",
                "params": {"f(t)": 1.0},
            }
        ],
        "cross_sections": [],
        "materials": [],
        "bcs": [],
        "element_mapping": {},
        "selected_mesh_id": "",
        "solver_preset": "linear-static-vtk",
        "oofem_executable": "",
        "last_input_file": "",
    }


def _next_id(prefix, used_ids):
    number = 1
    while "{}-{}".format(prefix, number) in used_ids:
        number += 1
    value = "{}-{}".format(prefix, number)
    used_ids.add(value)
    return value


def _constant_time_function_id(time_functions):
    for time_function in time_functions:
        if (
            isinstance(time_function, dict)
            and time_function.get("oofem_type") == "ConstantFunction"
            and time_function.get("id")
        ):
            return time_function["id"]
    return None


def _ensure_analysis(project):
    analysis = project.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
        project["analysis"] = analysis
    analysis.setdefault("id", _ANALYSIS_ID)
    analysis.setdefault("oofem_type", "StaticStructural")
    params = analysis.get("params")
    if not isinstance(params, dict):
        params = {}
        analysis["params"] = params
    params.setdefault("nsteps", 1)


def _ensure_time_functions(project):
    time_functions = project.get("time_functions")
    if not isinstance(time_functions, list):
        time_functions = []
        project["time_functions"] = time_functions

    time_function_id = _constant_time_function_id(time_functions)
    if time_function_id is None:
        used_ids = {
            item.get("id")
            for item in time_functions
            if isinstance(item, dict) and item.get("id")
        }
        time_function_id = (
            _TIME_FUNCTION_ID
            if _TIME_FUNCTION_ID not in used_ids
            else _next_id("ltf", used_ids)
        )
        time_functions.append(
            {
                "id": time_function_id,
                "oofem_type": "ConstantFunction",
                "params": {"f(t)": 1.0},
            }
        )
    return time_function_id


def _as_array(value):
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _migrate_boundary_conditions(project, default_time_function_id):
    boundary_conditions = project.get("bcs")
    if not isinstance(boundary_conditions, list):
        boundary_conditions = []
        project["bcs"] = boundary_conditions

    prescribed_types = {"BoundaryCondition", "Displacement"}
    for boundary_condition in boundary_conditions:
        if not isinstance(boundary_condition, dict):
            continue
        params = boundary_condition.get("params")
        if not isinstance(params, dict):
            params = {}
            boundary_condition["params"] = params

        # Accept both the scalar GUI records and the array spelling used by
        # early prototypes.  Canonical v2 keys are lower-case arrays.
        if "dofs" not in params:
            if "dof" in params:
                params["dofs"] = _as_array(params["dof"])
            elif "DOFs" in params:
                params["dofs"] = _as_array(params["DOFs"])

        value_key = (
            "values"
            if boundary_condition.get("oofem_type") in prescribed_types
            else "components"
        )
        if value_key not in params:
            if "val" in params:
                params[value_key] = _as_array(params["val"])
            elif value_key == "values" and "Values" in params:
                params[value_key] = _as_array(params["Values"])
            elif value_key == "components" and "Components" in params:
                params[value_key] = _as_array(params["Components"])

        # These are known legacy aliases, not user extension fields.
        for legacy_key in ("dof", "val", "DOFs", "Values", "Components"):
            params.pop(legacy_key, None)
        boundary_condition.setdefault(
            "time_function_id", default_time_function_id
        )


def _migrate_materials_and_cross_sections(project):
    materials = project.get("materials")
    if not isinstance(materials, list):
        materials = []
        project["materials"] = materials

    used_material_ids = {
        material.get("id")
        for material in materials
        if isinstance(material, dict) and material.get("id")
    }
    for material in materials:
        if isinstance(material, dict) and not material.get("id"):
            material["id"] = _next_id("material", used_material_ids)

    cross_sections = project.get("cross_sections")
    if not isinstance(cross_sections, list):
        cross_sections = []
        project["cross_sections"] = cross_sections

    used_cross_section_ids = {
        cross_section.get("id")
        for cross_section in cross_sections
        if isinstance(cross_section, dict) and cross_section.get("id")
    }
    existing_assignments = {
        (cross_section.get("material_id"), cross_section.get("assigned_group"))
        for cross_section in cross_sections
        if isinstance(cross_section, dict)
    }

    for material in materials:
        if not isinstance(material, dict):
            continue
        assigned_group = material.get("assigned_group")
        if not assigned_group:
            continue
        material_id = material.get("id")
        assignment = (material_id, assigned_group)
        if assignment in existing_assignments:
            continue

        material_params = material.get("params")
        if not isinstance(material_params, dict):
            material_params = {}
        cross_section_params = {}
        if "A" in material_params:
            cross_section_params["area"] = deepcopy(material_params["A"])
        if "t" in material_params:
            cross_section_params["thick"] = deepcopy(material_params["t"])

        cross_section_id = _next_id("cs", used_cross_section_ids)
        cross_sections.append(
            {
                "id": cross_section_id,
                "name": "{} cross section".format(
                    material.get("name") or material_id
                ),
                "oofem_type": "SimpleCS",
                "material_id": material_id,
                "assigned_group": assigned_group,
                "params": cross_section_params,
            }
        )
        existing_assignments.add(assignment)


def migrate_project_state(state):
    """Return *state* as a canonical version-2 project without mutating it.

    Unknown top-level and nested extension keys are retained.  ``None`` is
    treated as an empty project; other non-dictionary values are rejected so
    corrupt state cannot silently become a valid, unrelated project.
    """
    if state is None:
        return new_project_state()
    if not isinstance(state, dict):
        raise TypeError("OOFEM project state must be a dictionary or None")

    project = deepcopy(state)
    defaults = new_project_state()
    for key in (
        "materials",
        "bcs",
        "element_mapping",
        "selected_mesh_id",
        "solver_preset",
        "oofem_executable",
        "last_input_file",
    ):
        if key not in project:
            project[key] = deepcopy(defaults[key])

    _ensure_analysis(project)
    time_function_id = _ensure_time_functions(project)
    _migrate_boundary_conditions(project, time_function_id)
    _migrate_materials_and_cross_sections(project)
    project["schema_version"] = PROJECT_SCHEMA_VERSION
    return project
