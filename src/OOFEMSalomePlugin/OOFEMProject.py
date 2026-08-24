"""Versioned, JSON-serializable OOFEM project data.

This module deliberately has no Qt or SALOME dependencies.  It defines the
canonical project shape independently of study storage and provides a pure
migration function for the flat state dictionaries used by the original GUI.
"""

from copy import deepcopy


PROJECT_SCHEMA_VERSION = 3
_MAX_SCHEMA_VERSION = 2**31 - 1

_ANALYSIS_ID = "analysis-1"
_TIME_FUNCTION_ID = "ltf-1"


def parse_project_schema_version(value):
    """Return a canonical non-negative JSON integer schema version."""
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SCHEMA_VERSION
    ):
        raise ValueError("OOFEM project schema_version is invalid")
    return value


def new_project_state():
    """Return a fresh, canonical version-3 project dictionary."""
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        # Kept deterministic in this pure schema layer.  The SALOME study
        # session assigns a uuid4 hex value once it owns a new project.
        "project_id": "",
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
        "initial_conditions": [],
        "contacts": [],
        "element_mapping": {},
        "selected_mesh_id": "",
        "solver_preset": "linear-static-vtk",
        "oofem_executable": "",
        "last_input_file": "",
        "last_run_id": "",
        "run_history_root": "",
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
        # early prototypes.  Canonical v3 keys are lower-case arrays.
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


def _migrate_initial_conditions(project):
    initial_conditions = project.get("initial_conditions")
    if not isinstance(initial_conditions, list):
        initial_conditions = []
        project["initial_conditions"] = initial_conditions

    allocated_ids = {
        initial_condition.get("id")
        for initial_condition in initial_conditions
        if isinstance(initial_condition, dict) and initial_condition.get("id")
    }
    seen_ids = set()
    for initial_condition in initial_conditions:
        if not isinstance(initial_condition, dict):
            continue
        condition_id = initial_condition.get("id")
        if (
            not condition_id
            or condition_id in seen_ids
        ):
            condition_id = _next_id("ic", allocated_ids)
            initial_condition["id"] = condition_id
        seen_ids.add(condition_id)
        initial_condition.setdefault("oofem_type", "InitialCondition")

        params = initial_condition.get("params")
        if not isinstance(params, dict):
            params = {}
            initial_condition["params"] = params

        if "dofs" not in params:
            if "dof" in params:
                params["dofs"] = _as_array(params["dof"])
            elif "DOFs" in params:
                params["dofs"] = _as_array(params["DOFs"])

        # The OOFEM compatibility record stores constant initial values in a
        # dictionary keyed by value mode: u=total, v=velocity, a=acceleration.
        # Accept the early scalar spelling while keeping this dictionary as
        # the canonical project representation.
        if "conditions" not in params:
            legacy_value = None
            has_legacy_value = False
            for key in ("value", "val"):
                if key in params:
                    legacy_value = params[key]
                    has_legacy_value = True
                    break
            if has_legacy_value:
                params["conditions"] = {
                    str(params.get("mode", "u")).lower(): legacy_value
                }

        for legacy_key in ("dof", "DOFs", "value", "val", "mode"):
            params.pop(legacy_key, None)


def _migrate_contacts(project, default_time_function_id):
    contacts = project.get("contacts")
    if not isinstance(contacts, list):
        contacts = []
        project["contacts"] = contacts

    allocated_ids = {
        contact.get("id")
        for contact in contacts
        if isinstance(contact, dict) and contact.get("id")
    }
    seen_ids = set()
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        contact_id = contact.get("id")
        if not contact_id or contact_id in seen_ids:
            contact_id = _next_id("contact", allocated_ids)
            contact["id"] = contact_id
        seen_ids.add(contact_id)

        contact.setdefault("oofem_type", "StructuralPenaltyContactBC")
        if "master" in contact:
            if "master_group" not in contact:
                contact["master_group"] = contact["master"]
            contact.pop("master", None)
        if "slave" in contact:
            if "slave_group" not in contact:
                contact["slave_group"] = contact["slave"]
            contact.pop("slave", None)
        contact.setdefault("time_function_id", default_time_function_id)

        params = contact.get("params")
        if not isinstance(params, dict):
            params = {}
            contact["params"] = params
        aliases = {
            "pn": "normal_penalty",
            "pt": "tangential_penalty",
            "algo": "algorithm",
        }
        for legacy_key, canonical_key in aliases.items():
            if legacy_key in params:
                if canonical_key not in params:
                    params[canonical_key] = params[legacy_key]
                params.pop(legacy_key, None)
        params.setdefault("normal_penalty", 1.0e6)
        params.setdefault("tangential_penalty", 1.0e6)
        params.setdefault("friction", 0.0)
        params.setdefault("algorithm", 0)
        params.setdefault("two_pass", False)
        params.setdefault("reverse_master", False)
        params.setdefault("reverse_slave", False)


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

    for cross_section in cross_sections:
        if not isinstance(cross_section, dict):
            continue
        element_options = cross_section.get("element_options")
        if not isinstance(element_options, dict):
            element_options = {}
            cross_section["element_options"] = element_options
        element_options.setdefault("nlgeo", "inherit")

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
                "element_options": {"nlgeo": "inherit"},
                "params": cross_section_params,
            }
        )
        existing_assignments.add(assignment)


def migrate_project_state(state):
    """Return *state* as a canonical version-3 project without mutating it.

    The same additive migration accepts legacy unversioned, version-1, and
    version-2 states.  Unknown top-level and nested extension keys are
    retained.  ``None`` is treated as an empty project; other non-dictionary
    values are rejected so corrupt state cannot silently become a valid,
    unrelated project.
    """
    if state is None:
        return new_project_state()
    if not isinstance(state, dict):
        raise TypeError("OOFEM project state must be a dictionary or None")
    raw_version = state.get("schema_version")
    numeric_version = parse_project_schema_version(raw_version)
    if numeric_version is not None:
        if numeric_version > PROJECT_SCHEMA_VERSION:
            raise ValueError(
                "OOFEM project schema version {} is newer than supported "
                "version {}; refusing to downgrade it.".format(
                    numeric_version, PROJECT_SCHEMA_VERSION
                )
            )

    project = deepcopy(state)
    defaults = new_project_state()
    for key in (
        "project_id",
        "materials",
        "bcs",
        "initial_conditions",
        "contacts",
        "element_mapping",
        "selected_mesh_id",
        "solver_preset",
        "oofem_executable",
        "last_input_file",
        "last_run_id",
        "run_history_root",
    ):
        if key not in project:
            project[key] = deepcopy(defaults[key])

    _ensure_analysis(project)
    time_function_id = _ensure_time_functions(project)
    _migrate_boundary_conditions(project, time_function_id)
    _migrate_initial_conditions(project)
    _migrate_contacts(project, time_function_id)
    _migrate_materials_and_cross_sections(project)
    project["schema_version"] = PROJECT_SCHEMA_VERSION
    return project
