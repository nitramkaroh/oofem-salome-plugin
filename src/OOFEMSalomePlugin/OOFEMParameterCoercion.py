"""Shared text <-> typed-value coercion for editable parameter tables/dialogs.

Previously reimplemented separately (with drifting rigor -- only some
copies rejected non-finite floats) in OOFEMMainWidget, OOFEMBCDialog,
OOFEMCrossSectionDialog, and OOFEMTimeFunctionDialog. Keeping a single
implementation means a validation fix here applies everywhere a user can
type a material/BC/cross-section/time-function/analysis parameter.
"""

import math


def coerce_parameter_value(text, parameter_type):
    """Parse `text` as `parameter_type`, raising ValueError if invalid.

    Every numeric result is required to be finite: a stray 'nan' or 'inf'
    typed into a stiffness, thickness, area, or load value would otherwise
    reach the OOFEM input file unfiltered.
    """
    if parameter_type == "int":
        return int(text)
    if parameter_type == "float":
        return _coerce_finite_float(text)
    if parameter_type == "string":
        return str(text)
    if parameter_type in ("int_list", "float_list"):
        parts = [part.strip() for part in text.split(",")]
        if not parts or any(not part for part in parts):
            raise ValueError("expected comma-separated numbers")
        converter = int if parameter_type == "int_list" else _coerce_finite_float
        return [converter(part) for part in parts]
    if parameter_type == "mode_value_map":
        conditions = {}
        parts = [part.strip() for part in text.replace(";", ",").split(",")]
        if not parts or any(not part for part in parts):
            raise ValueError("expected entries such as u=0, v=1.5")
        for part in parts:
            if "=" not in part:
                raise ValueError("expected mode=value entries")
            mode, raw_value = (value.strip() for value in part.split("=", 1))
            mode = mode.lower()
            if mode not in ("u", "v", "a"):
                raise ValueError("mode must be u, v, or a")
            if mode in conditions:
                raise ValueError("mode '{}' is duplicated".format(mode))
            conditions[mode] = _coerce_finite_float(raw_value)
        return conditions
    raise ValueError("unsupported parameter type '{}'".format(parameter_type))


def _coerce_finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("expected a finite number")
    return value


def format_parameter_value(value, parameter_type):
    """Inverse of coerce_parameter_value(), for populating an editable cell."""
    if value is None:
        return ""
    if parameter_type in ("int_list", "float_list") and isinstance(
        value, (list, tuple)
    ):
        return ", ".join(str(item) for item in value)
    if parameter_type == "mode_value_map" and isinstance(value, dict):
        return ", ".join(
            "{}={}".format(key, value[key])
            for key in ("u", "v", "a")
            if key in value
        )
    return str(value)


def parameter_tooltip(parameter):
    """Compose a parameter's tooltip from its own text and OOFEM's manual.

    The hand-written ``description`` leads because it is phrased for someone
    meeting the parameter in this plugin.  ``doc_description``, harvested from
    the OOFEM manuals by tools/harvest_oofem_docs.py, follows when it says
    something more -- which values a mode selects, what happens when a limit is
    reached, which other parameter a default depends on.

    Rich text so the two read as separate paragraphs; Qt renders it in tooltips.
    """
    own = (parameter.get("description") or "").strip()
    manual = (parameter.get("doc_description") or "").strip()
    if not manual:
        return own
    escape = _html_escape
    if not own:
        return "<p>{}</p><p><i>OOFEM manual</i></p>".format(escape(manual))
    return (
        "<p>{}</p><p><i>OOFEM manual:</i> {}</p>".format(escape(own), escape(manual))
    )


def _html_escape(text):
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
