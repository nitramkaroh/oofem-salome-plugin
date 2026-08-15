"""Registration boundary between SALOME and the OOFEM user interface."""

import os

PLUGIN_NAME = "OOFEM"
PLUGIN_DESCRIPTION = "Prepare and export an OOFEM finite-element model"


def initialize_plugin(context):
    """Open (or raise) the OOFEM dock for the active SALOME study."""
    from OOFEMSalomePlugin.OOFEMModule import getModule

    return getModule().activate(context)


def _load_plugin_icon():
    """Return the packaged OOFEM icon using SALOME's active Qt binding."""
    try:
        from OOFEMSalomePlugin.OOFEMQt import QtGui
    except (ImportError, RuntimeError):
        return None

    icon_path = os.path.join(
        os.path.dirname(__file__), "resources", "icons", "oofem.png"
    )
    icon = QtGui.QIcon(icon_path)
    return None if icon.isNull() else icon


def register_plugin(add_function=None, icon=None):
    """Register the entry in SALOME's Tools > Plugins menu.

    ``add_function`` is injectable so discovery can be verified without a
    running SALOME desktop.
    """
    if add_function is None:
        from salome_pluginsmanager import AddFunction

        add_function = AddFunction
        if icon is None:
            icon = _load_plugin_icon()

    if icon is None:
        add_function(PLUGIN_NAME, PLUGIN_DESCRIPTION, initialize_plugin)
    else:
        add_function(PLUGIN_NAME, PLUGIN_DESCRIPTION, initialize_plugin, icon)
