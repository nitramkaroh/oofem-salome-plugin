"""SALOME light-module lifecycle adapter for the OOFEM user interface."""


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
    return getModule().activate(_context()) is not None


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
    """Save OOFEM project data for inclusion in the SALOME HDF study."""
    from OOFEMSalomePlugin.OOFEMModule import getModule

    try:
        return getModule().save(directory, url)
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
