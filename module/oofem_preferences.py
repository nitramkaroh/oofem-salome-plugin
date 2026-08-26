"""Typed SALOME preferences shared by the OOFEM light-module callbacks.

Keep this module independent from Qt and the OOFEM user interface. SALOME
loads module callbacks while its GUI is still being assembled, so importing
widgets here would make preference creation unnecessarily fragile.
"""

from __future__ import annotations

import os


RESOURCE_SECTION = "OOFEM"

SOLVER_EXECUTABLE = "solver_executable"
WORKING_DIRECTORY = "working_directory"
RESULTS_DIRECTORY = "results_directory"
SOLVER_TIMEOUT = "solver_timeout"
AUTO_OPEN_PARAVIS = "auto_open_paravis"

PREFERENCE_DEFAULTS = {
    SOLVER_EXECUTABLE: "",
    WORKING_DIRECTORY: "",
    RESULTS_DIRECTORY: "",
    SOLVER_TIMEOUT: 300,
    AUTO_OPEN_PARAVIS: False,
}

ENVIRONMENT_KEYS = {
    SOLVER_EXECUTABLE: "OOFEM_BIN",
    WORKING_DIRECTORY: "OOFEM_WORKING_DIRECTORY",
    RESULTS_DIRECTORY: "OOFEM_RESULTS_DIRECTORY",
    SOLVER_TIMEOUT: "OOFEM_SOLVER_TIMEOUT",
    AUTO_OPEN_PARAVIS: "OOFEM_AUTO_OPEN_PARAVIS",
}

# Qtx::PT_Directory. SALOME's Python bridge does not export PT_Directory,
# but PT_File supports changing the underlying QtxPathEdit through this
# documented item property.
DIRECTORY_PATH_TYPE = 2


def _api(api=None):
    if api is not None:
        return api
    import SalomePyQt

    return SalomePyQt.SalomePyQt()


def create_preferences(api=None, preference_types=None):
    """Create the OOFEM page in SALOME's common Preferences dialog.

    ``api`` and ``preference_types`` are injectable to keep this callback
    independently testable without a running SALOME desktop.
    """
    api = _api(api)
    if preference_types is None:
        import SalomePyQt as preference_types

    group = api.addPreference("Execution")

    solver = api.addPreference(
        "OOFEM executable",
        group,
        preference_types.PT_File,
        RESOURCE_SECTION,
        SOLVER_EXECUTABLE,
    )
    api.setPreferenceProperty(solver, "path_filter", "OOFEM executable (*)")

    for label, parameter in (
        ("Working directory", WORKING_DIRECTORY),
        ("Results directory", RESULTS_DIRECTORY),
    ):
        item = api.addPreference(
            label,
            group,
            preference_types.PT_File,
            RESOURCE_SECTION,
            parameter,
        )
        api.setPreferenceProperty(item, "path_type", DIRECTORY_PATH_TYPE)

    timeout = api.addPreference(
        "Solver timeout",
        group,
        preference_types.PT_IntSpin,
        RESOURCE_SECTION,
        SOLVER_TIMEOUT,
    )
    api.setPreferenceProperty(timeout, "min", 1)
    api.setPreferenceProperty(timeout, "max", 86400)
    api.setPreferenceProperty(timeout, "suffix", " s")

    api.addPreference(
        "Open results in integrated ParaView after a successful solve",
        group,
        preference_types.PT_Bool,
        RESOURCE_SECTION,
        AUTO_OPEN_PARAVIS,
    )
    return group


def read_preferences(api=None):
    """Read OOFEM settings through SALOME's typed resource API."""
    api = _api(api)
    values = {
        SOLVER_EXECUTABLE: api.stringSetting(
            RESOURCE_SECTION, SOLVER_EXECUTABLE, "", True
        ).strip(),
        WORKING_DIRECTORY: api.stringSetting(
            RESOURCE_SECTION, WORKING_DIRECTORY, "", True
        ).strip(),
        RESULTS_DIRECTORY: api.stringSetting(
            RESOURCE_SECTION, RESULTS_DIRECTORY, "", True
        ).strip(),
        SOLVER_TIMEOUT: max(
            1,
            api.integerSetting(
                RESOURCE_SECTION,
                SOLVER_TIMEOUT,
                PREFERENCE_DEFAULTS[SOLVER_TIMEOUT],
            ),
        ),
        AUTO_OPEN_PARAVIS: bool(
            api.boolSetting(
                RESOURCE_SECTION,
                AUTO_OPEN_PARAVIS,
                PREFERENCE_DEFAULTS[AUTO_OPEN_PARAVIS],
            )
        ),
    }
    for parameter in (SOLVER_EXECUTABLE, WORKING_DIRECTORY, RESULTS_DIRECTORY):
        if values[parameter]:
            values[parameter] = os.path.abspath(os.path.expanduser(values[parameter]))
    return values


def apply_environment(preferences, environ=None):
    """Expose preferences to the current and future runner implementations.

    Blank path preferences deliberately leave an existing launcher-provided
    value untouched. This preserves ``OOFEM_BIN`` compatibility.
    """
    environ = os.environ if environ is None else environ
    for parameter in (SOLVER_EXECUTABLE, WORKING_DIRECTORY, RESULTS_DIRECTORY):
        value = preferences.get(parameter, "")
        if value:
            environ[ENVIRONMENT_KEYS[parameter]] = str(value)
    timeout = preferences.get(SOLVER_TIMEOUT, PREFERENCE_DEFAULTS[SOLVER_TIMEOUT])
    environ[ENVIRONMENT_KEYS[SOLVER_TIMEOUT]] = str(max(1, int(timeout)))
    environ[ENVIRONMENT_KEYS[AUTO_OPEN_PARAVIS]] = (
        "1" if preferences.get(AUTO_OPEN_PARAVIS, False) else "0"
    )
    return environ


def refresh_environment(api=None, environ=None):
    """Read SALOME preferences and publish their compatibility environment."""
    preferences = read_preferences(api)
    apply_environment(preferences, environ)
    return preferences
