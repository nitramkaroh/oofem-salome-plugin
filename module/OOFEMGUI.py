"""SALOME light-module lifecycle adapter for the OOFEM user interface."""


def _prefer_bundled_package(module_file=None):
    """Keep a legacy fallback plugin from shadowing this native module.

    SALOME imports ``salome_plugins.py`` during startup.  An older fallback
    installation can therefore leave ``OOFEMSalomePlugin`` in ``sys.modules``
    before the selectable native module is activated.  The native adapter and
    its package are installed as siblings, so make that package authoritative
    without deleting or modifying the user's fallback files.
    """
    import os
    import sys

    module_root = os.path.realpath(
        os.path.dirname(module_file or __file__)
    )
    package_root = os.path.join(module_root, "OOFEMSalomePlugin")
    if not os.path.isdir(package_root):
        # Source-tree tests load module/OOFEMGUI.py while the package lives in
        # src/.  Their explicit path setup remains authoritative.
        return False

    normalized_root = os.path.normcase(module_root)
    sys.path[:] = [
        item
        for item in sys.path
        if os.path.normcase(os.path.realpath(item or os.curdir))
        != normalized_root
    ]
    sys.path.insert(0, module_root)

    def belongs_to_bundled(module):
        candidates = list(getattr(module, "__path__", ()) or ())
        origin = getattr(module, "__file__", None)
        if origin:
            candidates.append(origin)
        normalized_package = os.path.normcase(os.path.realpath(package_root))
        for candidate in candidates:
            candidate = os.path.normcase(os.path.realpath(candidate))
            try:
                if (
                    os.path.commonpath((candidate, normalized_package))
                    == normalized_package
                ):
                    return True
            except ValueError:
                continue
        return False

    loaded_names = [
        name
        for name in sys.modules
        if name == "OOFEMSalomePlugin"
        or name.startswith("OOFEMSalomePlugin.")
    ]
    if any(
        sys.modules.get(name) is not None
        and not belongs_to_bundled(sys.modules[name])
        for name in loaded_names
    ):
        for name in loaded_names:
            sys.modules.pop(name, None)
    return True


_prefer_bundled_package()


def _context():
    """Build the context expected by the shared plugin/module implementation."""
    import salome
    import SalomePyQt

    salome.salome_init()

    class Context:
        sg = SalomePyQt.SalomePyQt()
        study = salome.myStudy

    return Context()


def initialize():
    """Initialize the module; widgets are created lazily on activation."""


def activate():
    """Activate OOFEM and load SMESH without requiring a manual module switch."""
    from oofem_preferences import refresh_environment
    from OOFEMSalomePlugin.OOFEMModule import getModule

    refresh_environment()
    context = _context()
    dock = getModule().activate(context)
    if dock is None:
        return False
    create_root = getattr(context.sg, "createRoot", None)
    if callable(create_root):
        # A Python light module must register its data-model root while active.
        # Otherwise SALOME may omit saveFiles/openFiles payloads from the HDF
        # study even though the dock itself appears to work normally.
        create_root()
    return True


def deactivate():
    """Hide the OOFEM dock when another SALOME module is selected."""
    from OOFEMSalomePlugin.OOFEMModule import getModule

    getModule().deactivate()


def windows():
    """Request SALOME's Object Browser and Python Console dock windows."""
    from SalomePyQt import WT_ObjectBrowser, WT_PyConsole
    from OOFEMSalomePlugin.OOFEMQt import Qt

    return {
        WT_ObjectBrowser: Qt.LeftDockWidgetArea,
        WT_PyConsole: Qt.BottomDockWidgetArea,
    }


def views():
    """Keep the current viewer; OOFEM provides its workflow in a dock."""
    return []


def createPreferences():
    """Export typed OOFEM execution settings to SALOME Preferences."""
    from oofem_preferences import create_preferences

    create_preferences()


def preferenceChanged(section, name):
    """Refresh compatibility settings after an OOFEM preference changes."""
    del name
    if section != "OOFEM":
        return
    from oofem_preferences import refresh_environment

    refresh_environment()


def saveFiles(directory, url=""):
    """Flush the live editor and save it into the SALOME HDF study."""
    from OOFEMSalomePlugin.OOFEMModule import getModule

    try:
        module = getModule()
        return module.save(directory, url)
    except Exception:
        import traceback

        traceback.print_exc()
        return []


def openFiles(files, url=""):
    """Restore OOFEM project data extracted from a SALOME HDF study."""
    from OOFEMSalomePlugin.OOFEMModule import getModule

    try:
        return getModule().load(files, url)
    except Exception:
        import traceback

        traceback.print_exc()
        return False


def closeStudy():
    """Release OOFEM data belonging to the study SALOME is closing."""
    from OOFEMSalomePlugin.OOFEMModule import getModule

    getModule().close_study()
