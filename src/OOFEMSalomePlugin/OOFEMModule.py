import logging
import os

from OOFEMSalomePlugin.OOFEMQt import QtCore, QtWidgets
from OOFEMSalomePlugin.OOFEMDockWidget import OOFEMDockWidget
from OOFEMSalomePlugin.OOFEMDebugConsole import DebugConsole
from OOFEMSalomePlugin.OOFEMProject import (
    PROJECT_SCHEMA_VERSION,
    migrate_project_state,
    parse_project_schema_version,
)
from OOFEMSalomePlugin.OOFEMState import (
    OOFEMState,
    OOFEMStateVersionError,
    STATE_FILE_NAME,
)


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

    # 1. Check study ID methods / attributes on the proxy
    for name in (
        "_get_StudyId",
        "GetStudyId",
        "StudyId",
        "_get_studyId",
        "getStudyId",
        "GetID",
        "GetId",
        "getId",
    ):
        try:
            value = getattr(study, name)
            value = value() if callable(value) else value
        except Exception:
            continue
        if value is not None and value != "":
            return ("study-id", str(value))

    # 2. Check study URL (for saved HDF studies)
    for name in ("GetURL", "GetStudyUrl", "URL", "_get_URL", "_get_Url"):
        try:
            value = getattr(study, name)
            value = value() if callable(value) else value
        except Exception:
            continue
        if value is not None and str(value).strip() != "":
            return (
                "study-url",
                os.path.normcase(os.path.normpath(str(value).strip())),
            )

    # 3. Check salome.myStudyId if study is active
    try:
        import salome

        if (
            getattr(salome, "myStudy", None) is study
            or getattr(salome, "myStudy", None) == study
        ):
            study_id = getattr(salome, "myStudyId", None)
            if study_id is not None and study_id != "":
                return ("study-id", str(study_id))
    except Exception:
        pass

    # 4. Check study Name
    for name in ("GetName", "Name", "_get_Name"):
        try:
            value = getattr(study, name)
            value = value() if callable(value) else value
        except Exception:
            continue
        if value is not None and str(value).strip() != "":
            return ("study-name", str(value).strip())

    return ("study-object", id(study))


def _prepare_loaded_state(state):
    """Migrate only persisted legacy data; retain canonical object identity."""
    numeric_version = parse_project_schema_version(
        state.get("schema_version")
    )
    if numeric_version is not None and numeric_version > PROJECT_SCHEMA_VERSION:
        raise ValueError(
            "OOFEM project schema version {} is newer than supported version "
            "{}.".format(numeric_version, PROJECT_SCHEMA_VERSION)
        )
    is_legacy = (
        numeric_version is None or numeric_version < PROJECT_SCHEMA_VERSION
    )
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
        self._dock_visibility_generation = 0
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

    # SALOME restores its saved per-module window layout only after the
    # activate()/deactivate() callback has returned, so assert the state we
    # want repeatedly over a short window rather than once.
    _DOCK_VISIBILITY_DELAYS_MS = (0, 120, 400)

    def _reveal_dock(self):
        """Make the OOFEM dock visible and bring it to the front."""
        if self.dock is None:
            return False
        try:
            self.dock.show()
            self.dock.raise_()
        except Exception:
            _logger.debug("Could not reveal the OOFEM dock", exc_info=True)
            return False
        return True

    def _conceal_dock(self):
        """Hide the OOFEM dock."""
        if self.dock is None:
            return False
        try:
            self.dock.hide()
        except Exception:
            _logger.debug("Could not hide the OOFEM dock", exc_info=True)
            return False
        return True

    def _assert_dock_visibility(self, visible):
        """Apply a dock visibility now and hold it against SALOME's restore.

        LightApp_Application::loadDockWindowsState() calls
        desktop()->restoreState() with a layout keyed on the active
        module's name, and it runs *after* this module's activate() or
        deactivate() callback returns. A stale saved layout therefore
        silently undoes whatever we just did -- in both directions. Real
        example from this install: the layout had the dock hidden under
        the "OOFEM" key and visible under "nomodule", so activating the
        module appeared to do nothing, and deactivating it left the panel
        on screen.

        A generation counter makes the last call win, so a quick
        activate/deactivate pair cannot leave earlier timers fighting the
        newer intent.
        """
        self._dock_visibility_generation += 1
        generation = self._dock_visibility_generation
        applied = self._apply_dock_visibility(generation, visible)
        for delay in self._DOCK_VISIBILITY_DELAYS_MS:
            try:
                QtCore.QTimer.singleShot(
                    delay,
                    lambda g=generation, v=visible: self._apply_dock_visibility(
                        g, v
                    ),
                )
            except Exception:
                _logger.debug(
                    "Could not schedule the OOFEM dock visibility update",
                    exc_info=True,
                )
                break
        return applied

    def _apply_dock_visibility(self, generation, visible):
        """Apply one scheduled visibility intent, unless it was superseded."""
        if generation != self._dock_visibility_generation:
            return False
        return self._reveal_dock() if visible else self._conceal_dock()

    def _connect_widget_state(self):
        """Connect the live editor to this module instance exactly once."""
        if self.dock is None:
            return
        widget = self.dock.mainWidget
        signal = getattr(widget, "projectChanged", None)
        if signal is None:
            return
        if getattr(widget, "_oofem_state_sync_owner", None) is self:
            return
        signal.connect(self.sync_widget_state)
        widget._oofem_state_sync_owner = self

    def sync_widget_state(self, widget, mark_modified=True):
        """Store a live widget state in its owning per-study session."""
        state = getattr(widget, "state", None)
        study = getattr(widget, "study", None)
        if study is None or not isinstance(state, dict):
            return False
        session = self._ensure_session(study)
        session.state = state
        OOFEMState.save(study, state)
        if mark_modified and session.key == self._active_study_key:
            self._mark_study_modified()
        return True

    def activate(self, context=None):
        """Open the module for the study supplied by SALOME."""
        self.context = context or self.context
        main_window = None
        try:
            study = getattr(self.context, "study", None)
            session = self._select_study(study)
            if not session.state and study is not None:
                persisted = OOFEMState.load(study)
                if persisted:
                    try:
                        session.state = _prepare_loaded_state(persisted)
                    except Exception:
                        session.state = persisted
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
            self._connect_widget_state()
            self._assert_dock_visibility(True)
            self.dock.mainWidget.populateAll(study=study, state=session.state)
            if isinstance(self.dock.mainWidget.state, dict):
                session.state = self.dock.mainWidget.state
                OOFEMState.save(study, session.state)
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
        """Collect the active widget state immediately before SALOME saves."""
        if self.dock is not None:
            widget = self.dock.mainWidget
            widget_study = getattr(widget, "study", None)
            session = self._active_session()
            widget_matches = (
                widget_study is not None
                and _study_key(widget_study) == session.key
            )
            if widget_matches:
                try:
                    try:
                        widget.collectElementMapping(notify=False)
                    except TypeError:
                        widget.collectElementMapping()
                    try:
                        widget._solverSettingsChanged(notify=False)
                    except TypeError:
                        widget._solverSettingsChanged()
                except Exception:
                    _logger.warning(
                        "Could not collect all OOFEM widget settings",
                        exc_info=True,
                    )
                self._snapshot_widget_session()
                if widget_study is not None and isinstance(self.study_state, dict):
                    OOFEMState.save(widget_study, self.study_state)
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
        study = session.study or self._runtime_study()
        if study is not None:
            OOFEMState.save(study, state)
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
                    OOFEMState.save(refresh_study, session.state)
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
        version_error = None
        for name in preferred + [name for name in names if name not in preferred]:
            filename = name if os.path.isabs(name) else os.path.join(directory, name)
            try:
                state = OOFEMState.load_file(filename)
            except OOFEMStateVersionError as error:
                version_error = error
                continue
            if state is None:
                continue
            try:
                prepared_state = _prepare_loaded_state(state)
            except (TypeError, ValueError) as error:
                _logger.warning(
                    "Refusing to load an unsupported OOFEM project state: %s",
                    error,
                )
                return False
            self.study_url = url or ""
            self.set_study_state(prepared_state, refresh=True)
            return True
        if version_error is not None:
            _logger.warning("%s", version_error)
            try:
                QtWidgets.QMessageBox.warning(
                    None, "OOFEM Project", str(version_error)
                )
            except Exception:
                _logger.debug(
                    "Could not show the OOFEM version-mismatch dialog",
                    exc_info=True,
                )
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
        """Hide the OOFEM panel when SALOME switches the active module away.

        This mirrors how every other SALOME module behaves: the panel
        belongs to the module, so deselecting the module in the selector
        takes its windows away again. Held against SALOME's own layout
        restore, which would otherwise put the panel straight back if the
        saved "nomodule" layout happens to record it as visible.
        """
        self._assert_dock_visibility(False)


def getModule():
    """Return the process-wide module instance retained for Qt ownership."""
    global _oofem_module_instance
    if _oofem_module_instance is None:
        _oofem_module_instance = OOFEMModule()
    return _oofem_module_instance
