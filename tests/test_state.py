import json
import pathlib
import sys
import tempfile
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from OOFEMSalomePlugin.OOFEMState import (  # noqa: E402
    ATTR_NAME,
    COMPONENT_TYPE,
    STATE_ATTRIBUTE,
    STATE_FILE_FORMAT,
    STATE_FILE_NAME,
    STATE_FILE_VERSION,
    STATE_OBJECT_NAME,
    OOFEMState,
    OOFEMStateVersionError,
)


class FakeAttribute:
    def __init__(self):
        self.value = ""

    def SetValue(self, value):
        self.value = value

    def Value(self):
        return self.value


class FakeObject:
    def __init__(self, object_id):
        self.object_id = object_id
        self.name = ""
        self.children = []
        self.attributes = {}

    def GetName(self):
        return self.name


class FakeIterator:
    def __init__(self, children):
        self.children = children
        self.index = 0

    def More(self):
        return self.index < len(self.children)

    def Value(self):
        return self.children[self.index]

    def Next(self):
        self.index += 1


class FakeBuilder:
    def __init__(self, study):
        self.study = study

    def NewComponent(self, component_type):
        component = FakeObject(component_type)
        self.study.components[component_type] = component
        return component

    def SetName(self, study_object, name):
        study_object.name = name

    def NewObject(self, parent):
        study_object = FakeObject("object-{}".format(len(parent.children) + 1))
        parent.children.append(study_object)
        return study_object

    def FindOrCreateAttribute(self, study_object, attribute_type):
        return study_object.attributes.setdefault(attribute_type, FakeAttribute())

    def FindAttribute(self, study_object, attribute_type):
        attribute = study_object.attributes.get(attribute_type)
        return attribute is not None, attribute


class FakeStudy:
    def __init__(self):
        self.components = {}
        self.builder = FakeBuilder(self)

    def FindComponent(self, component_type):
        return self.components.get(component_type)

    def NewBuilder(self):
        return self.builder

    def NewChildIterator(self, component):
        return FakeIterator(component.children)


class LegacyStudy:
    def __init__(self):
        self.values = {}

    def GetString(self, key):
        return self.values.get(key, "")

    def SetString(self, key, value):
        self.values[key] = value


class CorbaLikeStudy:
    """Proxy shape that advertises legacy names but uses builder storage."""

    def __init__(self):
        self.legacy_calls = []

    def FindComponent(self, component_type):
        return None

    def NewBuilder(self):
        raise RuntimeError("standard storage unavailable")

    def GetString(self, key):
        self.legacy_calls.append(("get", key))
        raise AssertionError("CORBA Study.GetString must not be called")

    def SetString(self, key, value):
        self.legacy_calls.append(("set", key, value))
        raise AssertionError("CORBA Study.SetString must not be called")


class OOFEMStateTests(unittest.TestCase):
    def test_native_module_file_round_trip_is_versioned(self):
        expected = {"materials": [{"name": "rubber"}], "selected_mesh_id": "0:1:2"}
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / STATE_FILE_NAME
            self.assertTrue(OOFEMState.save_file(path, expected))
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["format"], STATE_FILE_FORMAT)
            self.assertEqual(document["version"], STATE_FILE_VERSION)
            self.assertEqual(document["state"], expected)
            self.assertEqual(OOFEMState.load_file(path), expected)

    def test_invalid_native_module_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / STATE_FILE_NAME
            path.write_text("not-json", encoding="utf-8")
            self.assertIsNone(OOFEMState.load_file(path))
            path.write_text(
                json.dumps({"format": "not-an-oofem-project", "state": {}}),
                encoding="utf-8",
            )
            self.assertIsNone(OOFEMState.load_file(path))

    def test_mismatched_envelope_version_raises_instead_of_silently_dropping(self):
        # A genuine OOFEM project file saved by an incompatible plugin
        # version must be reported as a specific, actionable failure, not
        # silently treated as if the study had no saved OOFEM state at all.
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / STATE_FILE_NAME
            path.write_text(
                json.dumps(
                    {"format": STATE_FILE_FORMAT, "version": 999, "state": {}}
                ),
                encoding="utf-8",
            )
            with self.assertRaises(OOFEMStateVersionError) as raised:
                OOFEMState.load_file(path)
            self.assertEqual(raised.exception.found_version, 999)

    def test_round_trip_uses_salome_attribute_string(self):
        study = FakeStudy()
        expected = {"materials": [{"name": "steel"}], "element_mapping": {"Triangle": "tr1"}}

        self.assertTrue(OOFEMState.save(study, expected))
        self.assertEqual(OOFEMState.load(study), expected)

        component = study.FindComponent(COMPONENT_TYPE)
        self.assertIsNotNone(component)
        self.assertEqual(len(component.children), 1)
        state_object = component.children[0]
        self.assertEqual(state_object.GetName(), STATE_OBJECT_NAME)
        raw = state_object.attributes[STATE_ATTRIBUTE].Value()
        self.assertEqual(json.loads(raw), expected)

    def test_repeated_save_reuses_component_and_state_object(self):
        study = FakeStudy()
        self.assertTrue(OOFEMState.save(study, {"value": 1}))
        self.assertTrue(OOFEMState.save(study, {"value": 2}))

        component = study.FindComponent(COMPONENT_TYPE)
        self.assertEqual(len(component.children), 1)
        self.assertEqual(OOFEMState.load(study), {"value": 2})

    def test_invalid_or_non_object_json_is_ignored(self):
        study = FakeStudy()
        OOFEMState.save(study, {"valid": True})
        component = study.FindComponent(COMPONENT_TYPE)
        attribute = component.children[0].attributes[STATE_ATTRIBUTE]

        attribute.SetValue("not-json")
        self.assertEqual(OOFEMState.load(study), {})
        attribute.SetValue("[1, 2]")
        self.assertEqual(OOFEMState.load(study), {})

    def test_legacy_test_double_remains_supported(self):
        study = LegacyStudy()
        self.assertTrue(OOFEMState.save(study, {"legacy": True}))
        self.assertIn(ATTR_NAME, study.values)
        self.assertEqual(OOFEMState.load(study), {"legacy": True})

    def test_corba_like_study_never_uses_legacy_string_methods(self):
        study = CorbaLikeStudy()

        self.assertEqual(OOFEMState.load(study), {})
        self.assertFalse(OOFEMState.save(study, {"value": 1}))
        self.assertEqual(study.legacy_calls, [])

    def test_none_and_non_dictionary_state_are_rejected(self):
        self.assertEqual(OOFEMState.load(None), {})
        self.assertFalse(OOFEMState.save(None, {}))
        self.assertFalse(OOFEMState.save(FakeStudy(), []))


if __name__ == "__main__":
    unittest.main()
