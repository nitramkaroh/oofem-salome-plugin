import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from xml.dom import minidom


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_ROOT = REPOSITORY_ROOT / "module"
PREFERENCES_PATH = MODULE_ROOT / "oofem_preferences.py"
REGISTRAR_PATH = MODULE_ROOT / "register_oofem_user_config.py"
sys.path.insert(0, str(MODULE_ROOT))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preferences = _load("test_oofem_preferences", PREFERENCES_PATH)


class FakePreferenceTypes:
    PT_File = 10
    PT_IntSpin = 20
    PT_Bool = 30


class FakeSalomePyQt:
    def __init__(self, values=None):
        self.values = values or {}
        self.items = []
        self.properties = {}

    def addPreference(self, *arguments):
        item = len(self.items) + 1
        self.items.append(arguments)
        return item

    def setPreferenceProperty(self, item, name, value):
        self.properties[(item, name)] = value

    def stringSetting(self, section, name, default, substitute):
        self._check_section(section)
        self.assert_substitution = substitute
        return self.values.get(name, default)

    def integerSetting(self, section, name, default):
        self._check_section(section)
        return self.values.get(name, default)

    def boolSetting(self, section, name, default):
        self._check_section(section)
        return self.values.get(name, default)

    @staticmethod
    def _check_section(section):
        if section != "OOFEM":
            raise AssertionError(section)


class SalomePreferenceTests(unittest.TestCase):
    def test_creates_typed_execution_preferences(self):
        api = FakeSalomePyQt()

        group = preferences.create_preferences(api, FakePreferenceTypes)

        self.assertEqual(group, 1)
        self.assertEqual(api.items[0], ("Execution",))
        self.assertEqual(
            [item[-1] for item in api.items[1:]],
            [
                preferences.SOLVER_EXECUTABLE,
                preferences.WORKING_DIRECTORY,
                preferences.RESULTS_DIRECTORY,
                preferences.SOLVER_TIMEOUT,
                preferences.AUTO_OPEN_PARAVIS,
            ],
        )
        self.assertEqual(api.items[1][2], FakePreferenceTypes.PT_File)
        self.assertEqual(api.items[2][2], FakePreferenceTypes.PT_File)
        self.assertEqual(api.items[3][2], FakePreferenceTypes.PT_File)
        self.assertEqual(api.items[4][2], FakePreferenceTypes.PT_IntSpin)
        self.assertEqual(api.items[5][2], FakePreferenceTypes.PT_Bool)
        self.assertEqual(
            api.properties[(3, "path_type")], preferences.DIRECTORY_PATH_TYPE
        )
        self.assertEqual(
            api.properties[(4, "path_type")], preferences.DIRECTORY_PATH_TYPE
        )
        self.assertEqual(api.properties[(5, "min")], 1)
        self.assertEqual(api.properties[(5, "max")], 86400)
        self.assertEqual(api.properties[(5, "suffix")], " s")

    def test_reads_typed_values_and_exports_compatibility_environment(self):
        api = FakeSalomePyQt(
            {
                preferences.SOLVER_EXECUTABLE: "/opt/oofem/bin/oofem",
                preferences.WORKING_DIRECTORY: "/work/oofem",
                preferences.RESULTS_DIRECTORY: "/results/oofem",
                preferences.SOLVER_TIMEOUT: 0,
                preferences.AUTO_OPEN_PARAVIS: True,
            }
        )

        values = preferences.read_preferences(api)
        environment = {}
        preferences.apply_environment(values, environment)

        self.assertEqual(environment["OOFEM_BIN"], "/opt/oofem/bin/oofem")
        self.assertEqual(environment["OOFEM_WORKING_DIRECTORY"], "/work/oofem")
        self.assertEqual(environment["OOFEM_RESULTS_DIRECTORY"], "/results/oofem")
        self.assertEqual(environment["OOFEM_SOLVER_TIMEOUT"], "1")
        self.assertEqual(environment["OOFEM_AUTO_OPEN_PARAVIS"], "1")

    def test_blank_solver_preference_preserves_launcher_environment(self):
        environment = {"OOFEM_BIN": "/launcher/oofem"}
        values = dict(preferences.PREFERENCE_DEFAULTS)

        preferences.apply_environment(values, environment)

        self.assertEqual(environment["OOFEM_BIN"], "/launcher/oofem")
        self.assertEqual(environment["OOFEM_SOLVER_TIMEOUT"], "300")
        self.assertEqual(environment["OOFEM_AUTO_OPEN_PARAVIS"], "0")

    def test_user_config_defaults_do_not_overwrite_user_choice(self):
        registrar = _load("test_oofem_registrar", REGISTRAR_PATH)
        original = (
            "<!DOCTYPE document>\n"
            "<document>\n"
            ' <section name="OOFEM">\n'
            '  <parameter name="solver_executable" value="/chosen/oofem"/>\n'
            " </section>\n"
            "</document>\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / "SalomeApprc.9.16.0"
            config.write_text(original, encoding="utf-8")

            _, first_changed = registrar.register(pathlib.Path("/opt/SALOME"), config)
            _, second_changed = registrar.register(pathlib.Path("/opt/SALOME"), config)

            document = minidom.parse(str(config))
            parameters = {
                item.getAttribute("name"): item.getAttribute("value")
                for item in document.getElementsByTagName("parameter")
            }
            self.assertTrue(first_changed)
            self.assertFalse(second_changed)
            self.assertEqual(parameters["solver_executable"], "/chosen/oofem")
            self.assertEqual(parameters["solver_timeout"], "300")
            self.assertEqual(parameters["auto_open_paravis"], "false")
            self.assertEqual(
                config.with_name(config.name + ".before-oofem").read_text(
                    encoding="utf-8"
                ),
                original,
            )


@unittest.skipUnless(shutil.which("cmake"), "cmake is not available")
class SalomeCMakeManifestTests(unittest.TestCase):
    def test_cmake_installs_isolated_module_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            build = root / "build"
            prefix = root / "prefix"
            hooks = root / "extra.env.d"
            subprocess.run(
                [
                    "cmake",
                    "-S",
                    str(REPOSITORY_ROOT),
                    "-B",
                    str(build),
                    "-DCMAKE_INSTALL_PREFIX={}".format(prefix),
                    "-DOOFEM_INSTALL_ENV_HOOK_DIR={}".format(hooks),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["cmake", "--install", str(build)],
                check=True,
                capture_output=True,
                text=True,
            )

            python_root = prefix / "bin" / "salome"
            resource_root = prefix / "share" / "salome" / "resources" / "oofem"
            self.assertTrue((python_root / "OOFEMGUI.py").is_file())
            self.assertTrue((python_root / "oofem_preferences.py").is_file())
            self.assertTrue(
                (python_root / "OOFEMSalomePlugin" / "OOFEMExporter.py").is_file()
            )
            self.assertTrue((resource_root / "SalomeApp.xml").is_file())
            self.assertTrue((resource_root / "oofem.png").is_file())
            self.assertTrue((hooks / "oofem.py").is_file())
            xml = (resource_root / "SalomeApp.xml").read_text(encoding="utf-8")
            self.assertNotIn('section name="launch"', xml)
            self.assertIn('section name="OOFEM"', xml)
            self.assertIn('name="solver_timeout" value="300"', xml)
            self.assertEqual(list(prefix.rglob("*.orig")), [])
            self.assertEqual(list(prefix.rglob("*.rej")), [])


if __name__ == "__main__":
    unittest.main()


class UserConfigNameTests(unittest.TestCase):
    """SALOME's per-user resource file is named differently per platform.

    SUIT_ResourceMgr reads SalomeApp.xml.<version> on Windows and
    SalomeApprc.<version> everywhere else. Registering into the wrong one is
    silent: the file is written, the script exits zero, and the module is simply
    absent from SALOME's module selector.
    """

    def setUp(self):
        import importlib.util
        import os
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        module_dir = os.path.join(root, "module")
        # oofem_preferences sits beside the registrar and is imported by name
        self._saved_path = list(sys.path)
        sys.path.insert(0, module_dir)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), self._saved_path))
        spec = importlib.util.spec_from_file_location(
            "oofem_register_under_test",
            os.path.join(module_dir, "register_oofem_user_config.py"),
        )
        self.registrar = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.registrar)

    def _names(self, os_name):
        import os

        saved = os.name
        os.name = os_name
        try:
            return self.registrar._user_config_names("9.16.0")
        finally:
            os.name = saved

    def test_windows_prefers_the_xml_name(self):
        self.assertEqual(self._names("nt")[0], "SalomeApp.xml.9.16.0")

    def test_posix_prefers_the_rc_name(self):
        self.assertEqual(self._names("posix")[0], "SalomeApprc.9.16.0")

    def test_both_names_are_offered_on_either_platform(self):
        for os_name in ("nt", "posix"):
            self.assertEqual(
                sorted(self._names(os_name)),
                ["SalomeApp.xml.9.16.0", "SalomeApprc.9.16.0"],
                os_name,
            )
