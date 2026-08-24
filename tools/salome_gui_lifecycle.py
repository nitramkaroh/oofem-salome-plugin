#!/usr/bin/env python3
"""Drive native OOFEM save/reopen checks from inside a SALOME GUI session.

This script is launched by the opt-in test in
tests/test_salome_gui_acceptance.py.
It is not intended to be run with the system Python.
"""

import json
import os
from pathlib import Path
import threading
import traceback

import salome
import SalomePyQt

# Import the native callback first.  Its bootstrap deliberately makes the
# package installed beside it authoritative over any legacy fallback plugin.
import OOFEMGUI
from OOFEMSalomePlugin.OOFEMQt import QtCore, QtWidgets


RESULT_PREFIX = "OOFEM_SALOME_GUI_RESULT="
PHASE = os.environ.get("OOFEM_GUI_PHASE", "").strip().casefold()
HDF_PATH = Path(os.environ.get("OOFEM_GUI_HDF", ""))
RESULT_PATH = Path(os.environ.get("OOFEM_GUI_RESULT", ""))
RUN_ID = os.environ.get("OOFEM_GUI_RUN_ID", "").strip()
EXPECTED_PROJECT_ID = "gui-acceptance-{}".format(RUN_ID)
EXPECTED_EXECUTABLE = "/tmp/oofem-after-{}".format(RUN_ID)

application = QtWidgets.QApplication.instance()
gui = SalomePyQt.SalomePyQt()
callback_events = []
_finished = False


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def process_events(milliseconds=100):
    """Let queued Qt/SALOME work finish without sleeping the GUI thread."""
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(milliseconds, loop.quit)
    execute = getattr(loop, "exec", None)
    if execute is None:
        execute = loop.exec_
    execute()


def find_action(shortcut, require_enabled=True):
    matches = [
        action
        for action in gui.getDesktop().findChildren(QtWidgets.QAction)
        if action.shortcut().toString() == shortcut
    ]
    require(
        len(matches) == 1,
        "expected one {} action, found {}".format(shortcut, len(matches)),
    )
    if require_enabled:
        require(matches[0].isEnabled(), "{} action is disabled".format(shortcut))
    return matches[0]


def trigger_file_action(shortcut, filename):
    """Trigger a standard SALOME file action and operate its real QFileDialog."""
    action = find_action(shortcut)
    observed = {
        "shortcut": shortcut,
        "dialog": False,
        "accept_clicked": False,
        "attempts": 0,
    }

    def cancel_dialog(dialog, button_box):
        reject_buttons = [
            button
            for button in button_box.buttons()
            if button_box.buttonRole(button)
            in (
                QtWidgets.QDialogButtonBox.RejectRole,
                QtWidgets.QDialogButtonBox.DestructiveRole,
            )
        ]
        if reject_buttons:
            reject_buttons[0].click()
        else:
            dialog.close()

    def accept_dialog():
        observed["attempts"] += 1
        modal = application.activeModalWidget()
        dialog = None
        if isinstance(modal, QtWidgets.QFileDialog):
            dialog = modal
        elif modal is not None:
            dialog = modal.findChild(QtWidgets.QFileDialog)

        if dialog is None:
            if observed["attempts"] < 200:
                QtCore.QTimer.singleShot(25, accept_dialog)
            else:
                observed["error"] = "no QFileDialog appeared"
            return

        observed["dialog"] = True
        observed["class"] = dialog.metaObject().className()
        dialog.setDirectory(str(filename.parent))
        dialog.selectFile(filename.name)
        file_name_edit = dialog.findChild(QtWidgets.QLineEdit, "fileNameEdit")
        if file_name_edit is not None:
            file_name_edit.setText(filename.name)

        button_box = dialog.findChild(QtWidgets.QDialogButtonBox)
        if button_box is None:
            observed["error"] = "file dialog has no button box"
            dialog.close()
            return
        accept_buttons = [
            button
            for button in button_box.buttons()
            if button_box.buttonRole(button)
            == QtWidgets.QDialogButtonBox.AcceptRole
        ]
        if len(accept_buttons) != 1:
            observed["error"] = "file dialog has {} accept buttons".format(
                len(accept_buttons)
            )
            cancel_dialog(dialog, button_box)
            return

        def click_accept():
            button = accept_buttons[0]
            observed["accept_enabled"] = button.isEnabled()
            if not button.isEnabled():
                observed["error"] = "file dialog accept button is disabled"
                cancel_dialog(dialog, button_box)
                return
            observed["accept_clicked"] = True
            button.click()

        QtCore.QTimer.singleShot(100, click_accept)

    QtCore.QTimer.singleShot(25, accept_dialog)
    action.trigger()
    gui.processEvents()

    require(observed["dialog"], "{} opened no QFileDialog".format(shortcut))
    require("error" not in observed, observed.get("error"))
    require(observed["accept_clicked"], "file dialog accept was not clicked")
    return observed


def install_callback_tracking():
    original_save = OOFEMGUI.saveFiles
    original_open = OOFEMGUI.openFiles
    original_close = OOFEMGUI.closeStudy

    def save_files(directory, url=""):
        returned = original_save(directory, url)
        callback_events.append(
            {
                "callback": "saveFiles",
                "url": str(url),
                "returned": list(returned),
            }
        )
        return returned

    def open_files(files, url=""):
        returned = original_open(files, url)
        callback_events.append(
            {
                "callback": "openFiles",
                "url": str(url),
                "files": [str(item) for item in files],
                "returned": bool(returned),
            }
        )
        return returned

    def close_study():
        callback_events.append({"callback": "closeStudy"})
        return original_close()

    OOFEMGUI.saveFiles = save_files
    OOFEMGUI.openFiles = open_files
    OOFEMGUI.closeStudy = close_study


def module_origins():
    import OOFEMSalomePlugin

    installed_root = (
        Path(os.environ["OOFEM_ROOT_DIR"]) / "bin" / "salome"
    ).resolve()
    origins = {
        "callback": str(Path(OOFEMGUI.__file__).resolve()),
        "package": str(Path(OOFEMSalomePlugin.__file__).resolve()),
    }
    for label, raw_path in origins.items():
        path = Path(raw_path)
        try:
            common = os.path.commonpath((str(path), str(installed_root)))
        except ValueError:
            common = ""
        require(
            common == str(installed_root),
            "{} resolved outside installed module: {}".format(label, path),
        )
    return origins


def activate_oofem():
    require(application is not None, "SALOME has no QApplication")
    require(gui.getDesktop().isVisible(), "SALOME desktop is not visible")
    require(gui.activateModule("SMESH"), "SMESH module activation failed")
    process_events(250)
    require(gui.activateModule("OOFEM"), "OOFEM module activation failed")
    process_events(250)

    from OOFEMSalomePlugin.OOFEMModule import getModule

    module = getModule()
    require(module.dock is not None, "OOFEM activation created no dock")
    require(module.dock.isVisible(), "OOFEM dock is not visible")
    require(
        salome.myStudy.FindComponent("OOFEM") is not None,
        "OOFEM activate() created no data-model root",
    )
    return module, module.dock.mainWidget


def acceptance_state():
    from OOFEMSalomePlugin.OOFEMProject import new_project_state

    state = new_project_state()
    state["project_id"] = EXPECTED_PROJECT_ID
    state["analysis"]["params"]["nsteps"] = 2
    state["materials"] = [
        {
            "id": "material-1",
            "name": "GUI acceptance material",
            "oofem_type": "ElasticIsotropic2d",
            "params": {"E": 1000.0, "nu": 0.25, "t": 1.0},
        }
    ]
    state["cross_sections"] = [
        {
            "id": "cross-section-1",
            "name": "GUI acceptance section",
            "oofem_type": "SimpleCS",
            "material_id": "material-1",
            "assigned_group": "BODIES",
            "element_options": {"nlgeo": "off"},
            "params": {"thick": 1.0},
        }
    ]
    state["contacts"] = [
        {
            "id": "contact-1",
            "name": "GUI acceptance contact",
            "oofem_type": "StructuralPenaltyContactBC",
            "master_group": "MASTER",
            "slave_group": "SLAVE",
            "time_function_id": "ltf-1",
            "params": {
                "normal_penalty": 10000.0,
                "tangential_penalty": 10000.0,
                "friction": 0.0,
                "algorithm": 0,
                "two_pass": False,
                "reverse_master": False,
                "reverse_slave": False,
            },
        }
    ]
    state["oofem_executable"] = "/tmp/oofem-before-{}".format(RUN_ID)
    return state


def run_save_phase(result):
    require(not HDF_PATH.exists(), "test HDF already exists")
    module, widget = activate_oofem()
    require(not hasattr(widget, "saveBtn"), "manual Commit button is present")
    require(
        widget.openParaVisBtn.text() == "Open in ParaView",
        "result button is not labelled ParaView",
    )

    state = acceptance_state()
    require(module.set_study_state(state, refresh=True), "state setup failed")
    require(
        widget.state.get("project_id") == EXPECTED_PROJECT_ID,
        "widget did not accept the test project",
    )

    save_as = trigger_file_action("Ctrl+Shift+S", HDF_PATH)
    require(HDF_PATH.is_file(), "Save As created no HDF file")
    require(HDF_PATH.stat().st_size > 0, "Save As created an empty HDF file")
    saves = [event for event in callback_events if event["callback"] == "saveFiles"]
    require(len(saves) == 1, "Save As did not call saveFiles exactly once")
    require(not gui.isModified(), "study stayed dirty after Save As")

    changes = []
    widget.projectChanged.connect(lambda changed: changes.append(changed))
    widget.oofemExecutableEdit.setText(EXPECTED_EXECUTABLE)
    widget.oofemExecutableEdit.editingFinished.emit()
    gui.processEvents()
    require(changes and changes[-1] is widget, "GUI edit emitted no projectChanged")
    require(gui.isModified(), "GUI edit did not mark the light module dirty")
    require(
        module.study_state.get("oofem_executable") == EXPECTED_EXECUTABLE,
        "GUI edit was not synchronized into the study session",
    )

    save_action = find_action("Ctrl+S")
    saves_before = len(saves)
    save_action.trigger()
    process_events(750)
    saves = [event for event in callback_events if event["callback"] == "saveFiles"]
    require(
        len(saves) == saves_before + 1,
        "Ctrl+S did not invoke the second saveFiles callback",
    )
    require(not gui.isModified(), "study stayed dirty after Ctrl+S")

    result.update(
        {
            "hdf_bytes": HDF_PATH.stat().st_size,
            "save_as_dialog": save_as,
            "save_callbacks": len(saves),
            "dirty_bridge": True,
            "project_id": module.study_state["project_id"],
            "oofem_executable": module.study_state["oofem_executable"],
            "nlgeo": module.study_state["cross_sections"][0]["element_options"][
                "nlgeo"
            ],
            "contacts": len(module.study_state["contacts"]),
            "manual_commit_absent": True,
            "paraview_button": widget.openParaVisBtn.text(),
        }
    )


def run_reopen_phase(result):
    require(HDF_PATH.is_file(), "saved HDF is missing")
    module, widget = activate_oofem()
    opened = [
        event
        for event in callback_events
        if event["callback"] == "openFiles" and event["returned"]
    ]
    require(opened, "activation did not restore OOFEM through openFiles")

    state = module.study_state
    result.update(
        {
            "actual_project_id": state.get("project_id"),
            "actual_oofem_executable": state.get("oofem_executable"),
        }
    )
    require(
        module.study_url == str(HDF_PATH),
        "module study URL does not match the reopened HDF",
    )
    require(
        state.get("project_id") == EXPECTED_PROJECT_ID,
        "reopened HDF lost the project marker",
    )
    require(
        state.get("oofem_executable") == EXPECTED_EXECUTABLE,
        "reopened HDF lost the live Ctrl+S edit",
    )
    require(
        state["cross_sections"][0]["element_options"]["nlgeo"] == "off",
        "reopened HDF lost element nlgeo=off",
    )
    require(len(state.get("contacts", [])) == 1, "reopened HDF lost contact")
    require(
        widget.state.get("project_id") == EXPECTED_PROJECT_ID,
        "widget did not receive the reopened project",
    )
    require(
        widget.oofemExecutableEdit.text() == EXPECTED_EXECUTABLE,
        "widget did not receive the reopened executable",
    )

    result.update(
        {
            "open_callbacks": len(opened),
            "study_url": module.study_url,
            "project_id": state["project_id"],
            "oofem_executable": state["oofem_executable"],
            "nlgeo": state["cross_sections"][0]["element_options"]["nlgeo"],
            "contacts": len(state["contacts"]),
            "widget_restored": True,
        }
    )


def shutdown_session():
    try:
        session = salome.naming_service.Resolve("/Kernel/Session")
        session.Shutdown()
    except Exception:
        traceback.print_exc()


def finish(result, error=None):
    global _finished
    if _finished:
        return
    _finished = True
    result["callbacks"] = callback_events
    if error is None:
        result["ok"] = True
    else:
        result["ok"] = False
        result["error"] = "{}: {}".format(type(error).__name__, error)
        result["traceback"] = traceback.format_exc()

    payload = json.dumps(result, sort_keys=True)
    try:
        RESULT_PATH.write_text(payload, encoding="utf-8")
    except Exception:
        traceback.print_exc()
    print(RESULT_PREFIX + payload, flush=True)
    threading.Thread(target=shutdown_session, daemon=True).start()


def run():
    result = {
        "phase": PHASE,
        "run_id": RUN_ID,
        "hdf": str(HDF_PATH),
        "origins": {},
    }
    try:
        require(PHASE in ("save", "reopen"), "invalid OOFEM_GUI_PHASE")
        require(bool(RUN_ID), "OOFEM_GUI_RUN_ID is empty")
        require(str(HDF_PATH), "OOFEM_GUI_HDF is empty")
        require(str(RESULT_PATH), "OOFEM_GUI_RESULT is empty")
        salome.salome_init()
        result["origins"] = module_origins()
        if PHASE == "save":
            run_save_phase(result)
        else:
            run_reopen_phase(result)
    except Exception as error:
        finish(result, error)
    else:
        finish(result)


try:
    install_callback_tracking()
except Exception as startup_error:
    QtCore.QTimer.singleShot(
        2500,
        lambda error=startup_error: finish(
            {"phase": PHASE, "run_id": RUN_ID, "hdf": str(HDF_PATH)},
            error,
        ),
    )
else:
    QtCore.QTimer.singleShot(
        50000,
        lambda: finish(
            {"phase": PHASE, "run_id": RUN_ID, "hdf": str(HDF_PATH)},
            TimeoutError("GUI acceptance deadline expired"),
        ),
    )
    QtCore.QTimer.singleShot(2500, run)
