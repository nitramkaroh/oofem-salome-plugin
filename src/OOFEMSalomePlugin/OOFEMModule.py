import logging
import os

from OOFEMSalomePlugin.OOFEMQt import QtCore, QtWidgets
from OOFEMSalomePlugin.OOFEMDockWidget import OOFEMDockWidget
from OOFEMSalomePlugin.OOFEMDebugConsole import DebugConsole
from OOFEMSalomePlugin.OOFEMProject import (
    PROJECT_SCHEMA_VERSION,
    migrate_project_state,
)
from OOFEMSalomePlugin.OOFEMState import OOFEMState, STATE_FILE_NAME


_oofem_module_instance = None
_logger = logging.getLogger("OOFEMSalomePlugin")
_NO_STUDY_KEY = ("no-study",)


class _StudySession:
    """Runtime-only data owned by one SALOME study."""

    def __init__(self, key, study=None):
        self.key = key
        # Keeping the proxy alive also makes the id()-based fallback key safe
        # from object-id reuse until this session is explicitly discarded.
        self.study = study
        self.state = {}
        self.url = ""


def _study_key(study):
    """Return a stable, hashable key for a SALOME study proxy."""
    if study is None:
        return _NO_STUDY_KEY

    # ``_get_StudyId`` is the omniORB spelling used by SALOMEDS.  The other
    # names make the resolver work with local wrappers and test doubles too.
    for name in ("_get_StudyId", "GetStudyId", "StudyId"):
        try:
            value = getattr(study, name)
            value = value() if callable(value) else value
        except Exception:
            continue
        if value is not None and value != "":
            return ("study-id", str(value))

    return ("study-object", id(study))


def _prepare_loaded_state(state):
    """Migrate only persisted legacy data; retain canonical object identity."""
    version = state.get("schema_version")
    try:
        is_legacy = version is None or int(version) < PROJECT_SCHEMA_VERSION
    except (TypeError, ValueError):
        is_legacy = True
    if is_legacy:
        return migrate_project_state(state)
    return state


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
        self._study_sessions = {}
        self._active_study_key = _NO_STUDY_KEY
        self._ensure_session(None)

    def _ensure_session(self, study):
        key = _study_key(study)
        session = self._study_sessions.get(key)
        if session is None:
            session = _StudySession(key, study)
            self._study_sessions[key] = session
        elif study is not None:
            # A CORBA proxy may be recreated while still representing the same
            # study id.  Retain the newest proxy for widget refreshes.
            session.study = study
        return session

    def _active_session(self):
        session = self._study_sessions.get(self._active_study_key)
        if session is None:
            session = self._ensure_session(None)
            self._active_study_key = session.key
        return session

    @property
    def study_state(self):
        """Backward-compatible view of the active study's state dictionary."""
        return self._active_session().state

    @study_state.setter
    def study_state(self, state):
        self._active_session().state = state

    @property
    def study_url(self):
        return self._active_session().url

    @study_url.setter
    def study_url(self, url):
        self._active_session().url = url or ""

    def _snapshot_widget_session(self):
        """Keep edits made in the dock before switching to another study."""
        if self.dock is None:
            return
        widget = self.dock.mainWidget
        session = self._active_session()
        widget_study = getattr(widget, "study", None)
        if session.study is not None and (
            widget_study is None or _study_key(widget_study) != session.key
        ):
            return
        if session.study is None and widget_study is not None:
            return
        if isinstance(getattr(widget, "state", None), dict):
            session.state = widget.state

    def _select_study(self, study, snapshot=True):
        key = _study_key(study)
        if key != self._active_study_key:
            if snapshot:
                self._snapshot_widget_session()
            self._active_study_key = key
        return self._ensure_session(study)

    def _runtime_study(self):
        """Resolve the study for callbacks that SALOME invokes without context."""
        try:
            import salome

            study = getattr(salome, "myStudy", None)
            if study is not None:
                return study
        except Exception:
            _logger.debug("Active SALOME study lookup failed", exc_info=True)

        study = getattr(self.context, "study", None)
        if study is not None:
            return study
        if self.dock is not None:
            return getattr(self.dock.mainWidget, "study", None)
        return None

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
            session = self._select_study(study)
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
            self.dock.mainWidget.populateAll(study=study, state=session.state)
            if isinstance(self.dock.mainWidget.state, dict):
                session.state = self.dock.mainWidget.state
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
            self._snapshot_widget_session()
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
        session = self._active_session()
        session.state = state
        if self.dock is not None:
            widget = self.dock.mainWidget
            widget_study = getattr(widget, "study", None)
            refresh_study = widget_study if widget_study is not None else session.study
            refresh_matches = (
                refresh_study is not None
                and _study_key(refresh_study) == session.key
            )
            if refresh and refresh_matches:
                widget.populateAll(study=refresh_study, state=state)
                if isinstance(widget.state, dict):
                    session.state = widget.state
            elif not refresh:
                widget.state = state
        if mark_modified:
            self._mark_study_modified()
        return True

    def save(self, directory, url=""):
        """Save state to a file that SALOME will embed in its HDF study."""
        study = self._runtime_study()
        if study is not None:
            self._select_study(study)
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
        study = self._runtime_study()
        if study is not None:
            self._select_study(study)
        directory = files[0]
        names = list(files[1:])
        preferred = [name for name in names if os.path.basename(name) == STATE_FILE_NAME]
        for name in preferred + [name for name in names if name not in preferred]:
            filename = name if os.path.isabs(name) else os.path.join(directory, name)
            state = OOFEMState.load_file(filename)
            if state is None:
                continue
            self.study_url = url or ""
            self.set_study_state(_prepare_loaded_state(state), refresh=True)
            return True
        return False

    def close_study(self):
        """Release all data associated with the study SALOME is closing."""
        study = self._runtime_study()
        if study is not None:
            self._select_study(study, snapshot=False)
        closed_key = self._active_study_key
        self._study_sessions.pop(closed_key, None)
        fallback = self._study_sessions.get(_NO_STUDY_KEY)
        if fallback is None:
            fallback = self._ensure_session(None)
        fallback.state = {}
        fallback.url = ""
        self._active_study_key = fallback.key
        widget_study = (
            getattr(self.dock.mainWidget, "study", None)
            if self.dock is not None
            else None
        )
        if self.dock is not None and (
            widget_study is None or _study_key(widget_study) == closed_key
        ):
            try:
                self.dock.mainWidget.cancelSolver(force=True)
            except AttributeError:
                # Test doubles and older docks may not expose the runner yet.
                pass
            except Exception:
                _logger.warning(
                    "Could not stop OOFEM while closing study", exc_info=True
                )
            self.dock.mainWidget.study = None
            self.dock.mainWidget.state = {}
        if getattr(self.context, "study", None) is not None:
            try:
                if _study_key(self.context.study) == closed_key:
                    self.context = None
            except Exception:
                self.context = None
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
