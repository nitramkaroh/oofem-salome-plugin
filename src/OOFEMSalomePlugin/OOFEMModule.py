import logging
import os

from OOFEMSalomePlugin.OOFEMQt import QtCore, QtWidgets
from OOFEMSalomePlugin.OOFEMDockWidget import OOFEMDockWidget
from OOFEMSalomePlugin.OOFEMDebugConsole import DebugConsole
from OOFEMSalomePlugin.OOFEMState import OOFEMState, STATE_FILE_NAME


_oofem_module_instance = None
_logger = logging.getLogger("OOFEMSalomePlugin")


def _find_main_window(context=None):
    """Resolve SALOME's desktop using its public API before Qt fallbacks."""
    if context is not None:
        try:
            desktop = context.sg.getDesktop()
            if desktop is not None:
                return desktop
        except Exception:
            _logger.debug("Plugin context has no SALOME desktop", exc_info=True)

    try:
        import SalomePyQt

        desktop = SalomePyQt.SalomePyQt().getDesktop()
        if desktop is not None:
            return desktop
    except Exception:
        _logger.debug("SalomePyQt desktop lookup failed", exc_info=True)

    application = QtWidgets.QApplication.instance()
    if application is None:
        return None
    active = application.activeWindow()
    if isinstance(active, QtWidgets.QMainWindow):
        return active
    for widget in application.topLevelWidgets():
        if isinstance(widget, QtWidgets.QMainWindow):
            return widget
    return None


class OOFEMModule:
    def __init__(self):
        self.dock = None
        self.debug_console = None
        self.context = None
        self.study_state = {}
        self.study_url = ""

    def _ensure_widgets(self, main_window):
        if self.dock is None:
            self.dock = main_window.findChild(
                QtWidgets.QDockWidget, OOFEMDockWidget.OBJECT_NAME
            )
        if self.dock is None:
            self.dock = OOFEMDockWidget(main_window)
            main_window.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.dock)

        if self.debug_console is None:
            self.debug_console = main_window.findChild(
                QtWidgets.QDockWidget, DebugConsole.OBJECT_NAME
            )
        if self.debug_console is None:
            self.debug_console = DebugConsole(main_window)
            main_window.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.debug_console)
            self.debug_console.hide()

    def activate(self, context=None):
        """Open the module for the study supplied by SALOME."""
        self.context = context or self.context
        main_window = None
        try:
            study = getattr(self.context, "study", None)
            from OOFEMSalomePlugin.OOFEMSalome import load_smesh_component

            try:
                load_smesh_component(study)
            except Exception:
                # A new study legitimately has no SMESH component. A stale or
                # temporarily unavailable CORBA proxy must not prevent module
                # activation either; the user can refresh after creating a mesh.
                _logger.warning("SMESH could not be loaded during activation", exc_info=True)
            main_window = _find_main_window(self.context)
            if main_window is None:
                _logger.error("Could not find the SALOME desktop window")
                return None
            self._ensure_widgets(main_window)
            self.dock.show()
            self.dock.raise_()
            self.dock.mainWidget.populateAll(study=study, state=self.study_state)
            self.study_state = self.dock.mainWidget.state
            return self.dock
        except Exception as error:
            _logger.exception("Failed to activate the OOFEM plugin")
            if main_window is not None:
                QtWidgets.QMessageBox.critical(
                    main_window,
                    "OOFEM Plugin",
                    "The OOFEM plugin could not be opened:\n{}".format(error),
                )
            return None

    def snapshot_state(self):
        """Collect the current JSON-serializable project state."""
        if self.dock is not None:
            widget = self.dock.mainWidget
            try:
                widget.collectElementMapping()
                widget._solverSettingsChanged()
            except Exception:
                _logger.warning("Could not collect all OOFEM widget settings", exc_info=True)
            if isinstance(widget.state, dict):
                self.study_state = widget.state
        return self.study_state

    def _mark_study_modified(self):
        """Ask SALOME to enable Save for the active study."""
        try:
            desktop_api = getattr(self.context, "sg", None)
            if desktop_api is not None and hasattr(desktop_api, "setModified"):
                desktop_api.setModified(True)
                return
        except Exception:
            _logger.debug("Context could not mark the SALOME study modified", exc_info=True)
        try:
            import SalomePyQt

            SalomePyQt.SalomePyQt().setModified(True)
        except Exception:
            _logger.debug("SALOME study-modified notification failed", exc_info=True)

    def set_study_state(self, state, refresh=False, mark_modified=False):
        """Install state loaded by SALOME or committed by the widget."""
        if not isinstance(state, dict):
            return False
        self.study_state = state
        if self.dock is not None:
            widget = self.dock.mainWidget
            if refresh and widget.study is not None:
                widget.populateAll(study=widget.study, state=state)
            else:
                widget.state = state
        if mark_modified:
            self._mark_study_modified()
        return True

    def save(self, directory, url=""):
        """Save state to a file that SALOME will embed in its HDF study."""
        state = self.snapshot_state()
        filename = os.path.join(directory, STATE_FILE_NAME)
        if not OOFEMState.save_file(filename, state):
            return []
        self.study_url = url or ""
        return [STATE_FILE_NAME]

    def load(self, files, url=""):
        """Restore state from files extracted by SALOME from an HDF study."""
        if not isinstance(files, (list, tuple)) or len(files) < 2:
            return False
        directory = files[0]
        names = list(files[1:])
        preferred = [name for name in names if os.path.basename(name) == STATE_FILE_NAME]
        for name in preferred + [name for name in names if name not in preferred]:
            filename = name if os.path.isabs(name) else os.path.join(directory, name)
            state = OOFEMState.load_file(filename)
            if state is None:
                continue
            self.study_url = url or ""
            self.set_study_state(state, refresh=True)
            return True
        return False

    def close_study(self):
        """Release all data associated with the study SALOME is closing."""
        self.study_state = {}
        self.study_url = ""
        if self.dock is not None:
            self.dock.mainWidget.study = None
            self.dock.mainWidget.state = {}
        self.deactivate()

    def showDebugConsole(self):
        if self.debug_console is not None:
            self.debug_console.show()
            self.debug_console.raise_()

    def deactivate(self):
        if self.dock is not None:
            self.dock.hide()


def getModule():
    """Return the process-wide module instance retained for Qt ownership."""
    global _oofem_module_instance
    if _oofem_module_instance is None:
        _oofem_module_instance = OOFEMModule()
    return _oofem_module_instance
