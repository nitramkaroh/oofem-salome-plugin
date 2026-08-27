"""Catalog and formatting utilities for OOFEM export modules (vtkxml)."""

from copy import deepcopy
from OOFEMSalomePlugin.OOFEMConfig import load_export_variable_catalog


def get_export_catalog():
    """Return the loaded export variable catalog dictionary."""
    return load_export_variable_catalog()


def get_primary_variables():
    """Return list of known UnknownType primary variables."""
    return get_export_catalog().get("primary_variables", [])


def get_internal_variables():
    """Return list of known InternalStateType internal variables."""
    return get_export_catalog().get("internal_variables", [])


def get_variable_categories():
    """Return dictionary of supported variable categories."""
    return get_export_catalog().get("categories", {})


def find_primary_variable(var_id_or_name):
    """Lookup a primary variable by integer ID or string name."""
    for item in get_primary_variables():
        if isinstance(var_id_or_name, int) and item.get("id") == var_id_or_name:
            return item
        if str(item.get("name", "")).lower() == str(var_id_or_name).lower():
            return item
        if str(item.get("id")) == str(var_id_or_name):
            return item
    return None


def find_internal_variable(var_id_or_name):
    """Lookup an internal variable by integer ID or string name."""
    for item in get_internal_variables():
        if isinstance(var_id_or_name, int) and item.get("id") == var_id_or_name:
            return item
        if str(item.get("name", "")).lower() == str(var_id_or_name).lower():
            return item
        if str(item.get("id")) == str(var_id_or_name):
            return item
    return None


def describe_variable(category, var_id, var_name=None):
    """Return a resolved dict with id, name, description, and category."""
    cat = str(category or "").strip().lower()
    try:
        numeric_id = int(var_id)
    except (TypeError, ValueError):
        numeric_id = 1

    if cat == "primvars":
        found = find_primary_variable(numeric_id)
        if found:
            name = found.get("name", var_name or "Unknown")
            desc = found.get("description", "")
        else:
            name = var_name or "UnknownType_{}".format(numeric_id)
            desc = "Custom primary variable (ID {})".format(numeric_id)
    else:
        found = find_internal_variable(numeric_id)
        if found:
            name = found.get("name", var_name or "Unknown")
            desc = found.get("description", "")
        else:
            name = var_name or "IST_Custom_{}".format(numeric_id)
            desc = "Custom internal state variable (ID {})".format(numeric_id)

    return {
        "category": cat,
        "id": numeric_id,
        "name": name,
        "description": desc,
    }


def format_vtk_record(variables, base_prefix="vtkxml tstep_all domain_all", stype=None):
    """Format a list of variable dictionaries into a valid vtkxml export record.

    *variables* is an iterable of dicts with keys {"category", "id"}.
    Categories are grouped in canonical OOFEM order:
      primvars, vars, cellvars, ipvars.
    """
    category_order = ("primvars", "vars", "cellvars", "ipvars")
    grouped = {cat: [] for cat in category_order}

    for item in variables or []:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category", "")).strip().lower()
        if cat not in grouped:
            cat = "cellvars"
        try:
            var_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if var_id not in grouped[cat]:
            grouped[cat].append(var_id)

    tokens = [base_prefix.strip()] if base_prefix and base_prefix.strip() else ["vtkxml", "tstep_all", "domain_all"]

    for cat in category_order:
        ids = grouped[cat]
        if ids:
            tokens.append(cat)
            tokens.append(str(len(ids)))
            tokens.extend(str(i) for i in ids)

    if stype is not None:
        try:
            tokens.extend(["stype", str(int(stype))])
        except (TypeError, ValueError):
            pass

    return " ".join(tokens)


def parse_vtk_record(record):
    """Parse a vtkxml command string into a list of variable dictionaries.

    Returns a list of dicts: [{"category": "primvars", "id": 1, "name": ..., "description": ...}, ...]
    """
    if not isinstance(record, str) or not record.strip():
        return []

    tokens = record.strip().split()
    variables = []
    known_keywords = {"primvars", "vars", "cellvars", "ipvars"}

    index = 0
    while index < len(tokens):
        token = tokens[index].lower()
        if token in known_keywords:
            if index + 1 < len(tokens):
                try:
                    count = int(tokens[index + 1])
                except ValueError:
                    count = 0
                ids = []
                for offset in range(count):
                    pos = index + 2 + offset
                    if pos < len(tokens):
                        try:
                            ids.append(int(tokens[pos]))
                        except ValueError:
                            pass
                for var_id in ids:
                    variables.append(describe_variable(token, var_id))
                index += 2 + count
                continue
        index += 1

    return variables


def default_variables_for_preset(preset_id):
    """Return canonical default variable dictionaries for a given solver preset ID."""
    preset = str(preset_id or "").strip().lower()
    if preset == "contact-vtk":
        return [
            describe_variable("primvars", 1, "DisplacementVector"),
            describe_variable("cellvars", 1, "IST_StressTensor"),
            describe_variable("cellvars", 150, "IST_ContactGap"),
            describe_variable("cellvars", 151, "IST_ContactPressure"),
            describe_variable("cellvars", 152, "IST_ContactStatus"),
        ]
    elif preset in ("text-only", "none"):
        return []
    else:  # "vtk", "custom", or standard default
        return [
            describe_variable("primvars", 1, "DisplacementVector"),
            describe_variable("cellvars", 1, "IST_StressTensor"),
        ]

