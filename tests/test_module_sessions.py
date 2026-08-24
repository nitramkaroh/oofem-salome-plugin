"""Per-study lifecycle tests for the SALOME light-module runtime."""

import os
import pathlib
import sys
import types
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import OOFEMSalomePlugin.OOFEMModule as module_mod
    import OOFEMSalomePlugin.OOFEMSalome as salome_mod

    _MODULE_IMPORT_ERROR = None
except Exception as error:  # pragma: no cover - environment without a Qt binding
    module_mod = None
    salome_mod = None
    _MODULE_IMPORT_ERROR = error


class FakeStudy:
    def __init__(self, study_id):
        self.study_id = study_id

    def _get_StudyId(self):
        return self.study_id


class ObjectOnlyStudy:
    pass


class FakeMainWidget:
    def __init__(self):
        self.study = None
        self.state = {}
        self.populate_calls = []
        self.cancel_calls = []

    def populateAll(self, study=None, state=None):
        self.study = study
        if isinstance(state, dict):
            self.state = state
        self.populate_calls.append((study, state))

    def collectElementMapping(self):
        pass

    def _solverSettingsChanged(self):
        pass

    def cancelSolver(self, force=False):
        self.cancel_calls.append(force)


class FakeDock:
    def __init__(self):
        self.mainWidget = FakeMainWidget()
        self.visible = False

    def show(self):
        self.visible = True

    def raise_(self):
        pass

    def hide(self):
        self.visible = False


@unittest.skipIf(
    module_mod is None, "Qt module unavailable: {}".format(_MODULE_IMPORT_ERROR)
)
class StudySessionTests(unittest.TestCase):
    def setUp(self):
        self.module = module_mod.OOFEMModule()
        self.module.dock = FakeDock()

    def activate(self, study):
        context = types.SimpleNamespace(study=study, sg=types.SimpleNamespace())
        with unittest.mock.patch.object(
            module_mod, "_find_main_window", return_value=object()
        ), unittest.mock.patch.object(
            salome_mod, "load_smesh_component", return_value=None
        ), unittest.mock.patch.object(
            self.module, "_ensure_widgets", return_value=None
        ), unittest.mock.patch.object(
            module_mod.QtWidgets.QMessageBox, "critical", return_value=0
        ):
            dock = self.module.activate(context)
        self.assertIs(dock, self.module.dock)

    def test_switching_studies_isolates_state_and_reuses_stable_study_id(self):
        study_a = FakeStudy(101)
        study_b = FakeStudy(202)
        state_a = {"schema_version": 2, "name": "A"}
        state_b = {"schema_version": 2, "name": "B"}

        self.activate(study_a)
        self.assertTrue(self.module.set_study_state(state_a))
        self.activate(study_b)
        self.assertEqual(self.module.study_state, {})
        self.assertTrue(self.module.set_study_state(state_b))

        # omniORB may hand out a new proxy object for the same SALOMEDS study.
        replacement_proxy_a = FakeStudy(101)
        self.activate(replacement_proxy_a)
        self.assertIs(self.module.study_state, state_a)
        self.assertIs(self.module.dock.mainWidget.state, state_a)
        self.assertIs(self.module.dock.mainWidget.study, replacement_proxy_a)

        self.activate(FakeStudy(202))
        self.assertIs(self.module.study_state, state_b)

    def test_object_identity_fallback_does_not_merge_unidentified_studies(self):
        study_a = ObjectOnlyStudy()
        study_b = ObjectOnlyStudy()
        state_a = {"schema_version": 2, "name": "object A"}

        self.activate(study_a)
        self.module.set_study_state(state_a)
        self.activate(study_b)
        self.assertEqual(self.module.study_state, {})
        self.activate(study_a)
        self.assertIs(self.module.study_state, state_a)

    def test_close_discards_only_active_study_session(self):
        state_a = {"schema_version": 2, "name": "A"}
        state_b = {"schema_version": 2, "name": "B"}

        self.activate(FakeStudy(1))
        self.module.set_study_state(state_a)
        self.activate(FakeStudy(2))
        self.module.set_study_state(state_b)
        self.activate(FakeStudy(1))

        self.module.close_study()
        self.assertEqual(self.module.study_state, {})
        self.assertIsNone(self.module.context)
        self.assertIsNone(self.module.dock.mainWidget.study)
        self.assertFalse(self.module.dock.visible)
        self.assertEqual(self.module.dock.mainWidget.cancel_calls, [True])

        self.activate(FakeStudy(1))
        self.assertEqual(self.module.study_state, {})
        self.activate(FakeStudy(2))
        self.assertIs(self.module.study_state, state_b)

    def test_open_files_before_activation_uses_salome_active_study(self):
        study_a = FakeStudy(41)
        study_b = FakeStudy(42)
        state_a = {"schema_version": 2, "name": "loaded A"}
        state_b = {"schema_version": 2, "name": "loaded B"}
        fake_salome = types.ModuleType("salome")
        self.module.dock = None

        with unittest.mock.patch.dict(sys.modules, {"salome": fake_salome}):
            fake_salome.myStudy = study_a
            with unittest.mock.patch.object(
                module_mod.OOFEMState, "load_file", return_value=state_a
            ):
                self.assertTrue(
                    self.module.load(["/unused", module_mod.STATE_FILE_NAME])
                )

            fake_salome.myStudy = study_b
            with unittest.mock.patch.object(
                module_mod.OOFEMState, "load_file", return_value=state_b
            ):
                self.assertTrue(
                    self.module.load(["/unused", module_mod.STATE_FILE_NAME])
                )

        self.module.dock = FakeDock()
        self.activate(FakeStudy(41))
        self.assertIs(self.module.study_state, state_a)
        self.activate(FakeStudy(42))
        self.assertIs(self.module.study_state, state_b)

    def test_save_files_serializes_salome_active_session(self):
        state_a = {"schema_version": 2, "name": "save A"}
        state_b = {"schema_version": 2, "name": "save B"}
        self.activate(FakeStudy(51))
        self.module.set_study_state(state_a)
        self.activate(FakeStudy(52))
        self.module.set_study_state(state_b)
        self.activate(FakeStudy(51))

        fake_salome = types.ModuleType("salome")
        fake_salome.myStudy = FakeStudy(52)
        saved = []
        with unittest.mock.patch.dict(sys.modules, {"salome": fake_salome}), \
             unittest.mock.patch.object(
                 module_mod.OOFEMState,
                 "save_file",
                 side_effect=lambda filename, state: saved.append(
                     (filename, state)
                 ) or True,
             ):
            self.assertEqual(
                self.module.save("/unused", "active-b.hdf"),
                [module_mod.STATE_FILE_NAME],
            )

        self.assertIs(saved[0][1], state_b)
        self.assertEqual(self.module.study_url, "active-b.hdf")
        self.activate(FakeStudy(51))
        self.assertIs(self.module.study_state, state_a)

    def test_load_migrates_legacy_but_preserves_canonical_v2_identity(self):
        self.activate(FakeStudy(303))
        canonical = {"schema_version": 2, "extension": {"value": 1}}
        with unittest.mock.patch.object(
            module_mod.OOFEMState, "load_file", return_value=canonical
        ):
            self.assertTrue(
                self.module.load(["/unused", module_mod.STATE_FILE_NAME], "study.hdf")
            )
        self.assertIs(self.module.study_state, canonical)
        self.assertEqual(self.module.study_url, "study.hdf")

        legacy = {
            "materials": [],
            "bcs": [],
            "custom_extension": {"kept": True},
        }
        with unittest.mock.patch.object(
            module_mod.OOFEMState, "load_file", return_value=legacy
        ):
            self.assertTrue(
                self.module.load(["/unused", module_mod.STATE_FILE_NAME], "legacy.hdf")
            )
        self.assertIsNot(self.module.study_state, legacy)
        self.assertEqual(self.module.study_state["schema_version"], 2)
        self.assertEqual(
            self.module.study_state["custom_extension"], {"kept": True}
        )
        self.assertNotIn("schema_version", legacy)


if __name__ == "__main__":
    unittest.main()
