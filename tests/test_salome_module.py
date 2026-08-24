import importlib
import importlib.util
import os
import pathlib
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_ROOT = REPOSITORY_ROOT / "src"
MODULE_ADAPTER = REPOSITORY_ROOT / "module" / "OOFEMGUI.py"
MODULE_ENV = REPOSITORY_ROOT / "module" / "oofem_env.py"
MODULE_INTEGRATION = REPOSITORY_ROOT / "module" / "SalomeApp.integration.xml"
USER_CONFIG_REGISTRAR = (
    REPOSITORY_ROOT / "module" / "register_oofem_user_config.py"
)
MODULE_INSTALLER = REPOSITORY_ROOT / "install-salome-module.sh"
sys.path.insert(0, str(SRC_ROOT))

from OOFEMSalomePlugin.OOFEMSalome import load_smesh_component  # noqa: E402


class SalomeLifecycleTests(unittest.TestCase):
    def test_native_adapter_prefers_own_package_over_loaded_fallback(self):
        spec = importlib.util.spec_from_file_location(
            "test_native_OOFEMGUI", MODULE_ADAPTER
        )
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "native"
            bundled = root / "OOFEMSalomePlugin"
            fallback = pathlib.Path(directory) / "fallback" / "OOFEMSalomePlugin"
            bundled.mkdir(parents=True)
            fallback.mkdir(parents=True)
            (bundled / "__init__.py").write_text(
                "ORIGIN = 'native'\n", encoding="utf-8"
            )

            fake_package = types.ModuleType("OOFEMSalomePlugin")
            fake_package.__file__ = str(fallback / "__init__.py")
            fake_package.__path__ = [str(fallback)]
            fake_child = types.ModuleType("OOFEMSalomePlugin.OOFEMQt")
            fake_child.__file__ = str(fallback / "OOFEMQt.py")

            package_names = [
                name
                for name in sys.modules
                if name == "OOFEMSalomePlugin"
                or name.startswith("OOFEMSalomePlugin.")
            ]
            previous_modules = {
                name: sys.modules[name] for name in package_names
            }
            previous_path = list(sys.path)
            for name in package_names:
                sys.modules.pop(name, None)
            sys.modules["OOFEMSalomePlugin"] = fake_package
            sys.modules["OOFEMSalomePlugin.OOFEMQt"] = fake_child
            try:
                self.assertTrue(
                    adapter._prefer_bundled_package(str(root / "OOFEMGUI.py"))
                )
                self.assertNotIn(
                    "OOFEMSalomePlugin.OOFEMQt", sys.modules
                )
                loaded = importlib.import_module("OOFEMSalomePlugin")
                self.assertEqual(loaded.ORIGIN, "native")
                self.assertEqual(
                    pathlib.Path(loaded.__file__).resolve(),
                    (bundled / "__init__.py").resolve(),
                )
                self.assertEqual(pathlib.Path(sys.path[0]).resolve(), root.resolve())
            finally:
                for name in list(sys.modules):
                    if name == "OOFEMSalomePlugin" or name.startswith(
                        "OOFEMSalomePlugin."
                    ):
                        sys.modules.pop(name, None)
                sys.modules.update(previous_modules)
                sys.path[:] = previous_path

    def test_loads_existing_smesh_component_like_asterstudy(self):
        calls = []
        component = object()
        engine = object()

        class Study:
            def FindComponent(self, name):
                calls.append(("find", name))
                return component

            def NewBuilder(self):
                return types.SimpleNamespace(
                    LoadWith=lambda supplied_component, supplied_engine: calls.append(
                        ("load", supplied_component, supplied_engine)
                    )
                )

        fake_salome = types.ModuleType("salome")
        fake_salome.lcc = types.SimpleNamespace(
            FindOrLoadComponent=lambda container, name: (
                calls.append(("engine", container, name)) or engine
            )
        )
        previous = sys.modules.get("salome")
        sys.modules["salome"] = fake_salome
        try:
            result = load_smesh_component(Study())
        finally:
            if previous is None:
                sys.modules.pop("salome", None)
            else:
                sys.modules["salome"] = previous

        self.assertIs(result, component)
        self.assertEqual(
            calls,
            [
                ("find", "SMESH"),
                ("engine", "FactoryServer", "SMESH"),
                ("load", component, engine),
            ],
        )

    def test_module_adapter_supplies_active_study_context(self):
        calls = []
        study = object()

        class DesktopApi:
            def createRoot(self):
                calls.append("create_root")

        desktop_api = DesktopApi()

        fake_salome = types.ModuleType("salome")
        fake_salome.myStudy = study
        fake_salome.salome_init = lambda: calls.append("salome_init")

        fake_salome_pyqt = types.ModuleType("SalomePyQt")
        fake_salome_pyqt.SalomePyQt = lambda: desktop_api

        fake_preferences = types.ModuleType("oofem_preferences")
        fake_preferences.refresh_environment = lambda: calls.append("preferences")

        class Module:
            dock = None

            def activate(self, context):
                calls.append(("activate", context.sg, context.study))
                return "dock"

            def deactivate(self):
                calls.append("deactivate")

        module = Module()
        fake_oofem_module = types.ModuleType("OOFEMSalomePlugin.OOFEMModule")
        fake_oofem_module.getModule = lambda: module

        replacements = {
            "salome": fake_salome,
            "SalomePyQt": fake_salome_pyqt,
            "oofem_preferences": fake_preferences,
            "OOFEMSalomePlugin.OOFEMModule": fake_oofem_module,
        }
        previous = {name: sys.modules.get(name) for name in replacements}
        sys.modules.update(replacements)
        try:
            spec = importlib.util.spec_from_file_location(
                "test_OOFEMGUI", MODULE_ADAPTER
            )
            adapter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(adapter)
            self.assertTrue(adapter.activate())
            adapter.deactivate()
            self.assertEqual(adapter.views(), [])
        finally:
            for name, old_module in previous.items():
                if old_module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old_module

        self.assertEqual(
            calls,
            [
                "preferences",
                "salome_init",
                ("activate", desktop_api, study),
                "create_root",
                "deactivate",
            ],
        )


class SalomeModuleInstallerTests(unittest.TestCase):
    def test_extra_environment_registers_oofem_first_after_base_launcher(self):
        calls = []

        class Context:
            def setVariable(self, name, value, overwrite=False):
                calls.append(("set", name, value, overwrite))

            def addToPath(self, value):
                calls.append(("path", value))

            def addToPythonPath(self, value):
                calls.append(("pythonpath", value))

            def appendVariable(self, name, value, separator=None):
                calls.append(("append", name, value, separator))

            def addToVariable(self, name, value, separator=None):
                calls.append(("prepend", name, value, separator))

        spec = importlib.util.spec_from_file_location("test_oofem_env", MODULE_ENV)
        environment = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(environment)
        previous_modules = os.environ.get("SALOME_MODULES")
        os.environ["SALOME_MODULES"] = "SHAPER,GEOM,PARAVIS,YACS,JOBMANAGER"
        try:
            environment.init(Context(), "/opt/SALOME")
        finally:
            if previous_modules is None:
                os.environ.pop("SALOME_MODULES", None)
            else:
                os.environ["SALOME_MODULES"] = previous_modules

        module_root = "/opt/SALOME/INSTALL/OOFEM"
        self.assertIn(("set", "OOFEM_ROOT_DIR", module_root, True), calls)
        self.assertIn(
            (
                "set",
                "SALOME_MODULES",
                "OOFEM,SHAPER,GEOM,PARAVIS,YACS,JOBMANAGER",
                True,
            ),
            calls,
        )
        self.assertIn(
            (
                "prepend",
                "SalomeAppConfig",
                module_root + "/share/salome/resources/oofem",
                ":",
            ),
            calls,
        )

    def test_installs_light_module_and_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            salome_dir = pathlib.Path(directory) / "SALOME"
            salome_dir.mkdir()
            salome_launcher = salome_dir / "salome"
            salome_launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            salome_launcher.chmod(0o755)
            version_file = (
                salome_dir / "INSTALL" / "GUI" / "bin" / "salome" / "VERSION"
            )
            version_file.parent.mkdir(parents=True)
            version_file.write_text("[SALOME GUI]  : 9.16.0\n", encoding="utf-8")
            salome_resource = (
                salome_dir
                / "INSTALL"
                / "SALOME"
                / "share"
                / "salome"
                / "resources"
                / "salome"
                / "SalomeApp.xml"
            )
            salome_resource.parent.mkdir(parents=True)
            original_resource = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<document>\n"
                '  <section name="resources">\n'
                '    <parameter name="LightApp" value="/opt/salome"/>\n'
                "  </section>\n"
                "</document>\n"
            )
            salome_resource.write_text(original_resource, encoding="utf-8")

            environment = os.environ.copy()
            environment["XDG_CONFIG_HOME"] = str(
                pathlib.Path(directory) / "config"
            )
            user_config = (
                pathlib.Path(environment["XDG_CONFIG_HOME"])
                / "salome"
                / "SalomeApprc.9.16.0"
            )
            user_config.parent.mkdir(parents=True)
            original_user_config = (
                "<!DOCTYPE document>\n"
                "<document>\n"
                ' <section name="desktop">\n'
                '  <parameter name="geometry" value="800x600"/>\n'
                " </section>\n"
                ' <section name="OOFEM">\n'
                '  <parameter name="solver_executable" value="/custom/oofem"/>\n'
                " </section>\n"
                "</document>\n"
            )
            user_config.write_text(original_user_config, encoding="utf-8")

            first = subprocess.run(
                ["bash", str(MODULE_INSTALLER), "--salome", str(salome_dir)],
                cwd=str(REPOSITORY_ROOT),
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            second = subprocess.run(
                ["bash", str(MODULE_INSTALLER), "--salome", str(salome_dir)],
                cwd=str(REPOSITORY_ROOT),
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            module_root = salome_dir / "INSTALL" / "OOFEM"
            self.assertIn("OOFEM SALOME module installed", first.stdout)
            self.assertIn("Registered in per-user SALOME GUI resources", first.stdout)
            self.assertEqual(second.returncode, 0)
            self.assertTrue(
                (module_root / "bin" / "salome" / "OOFEMGUI.py").is_file()
            )
            self.assertEqual(
                (
                    module_root
                    / "bin"
                    / "salome"
                    / "register_oofem_user_config.py"
                ).read_text(encoding="utf-8"),
                USER_CONFIG_REGISTRAR.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (
                    module_root / "bin" / "salome" / "oofem_preferences.py"
                ).read_text(encoding="utf-8"),
                (REPOSITORY_ROOT / "module" / "oofem_preferences.py").read_text(
                    encoding="utf-8"
                ),
            )

            self.assertTrue(
                (
                    module_root
                    / "bin"
                    / "salome"
                    / "OOFEMSalomePlugin"
                    / "OOFEMSalome.py"
                ).is_file()
            )
            resource = (
                module_root / "share" / "salome" / "resources" / "oofem"
            )
            xml = (resource / "SalomeApp.xml").read_text(encoding="utf-8")
            self.assertIn('section name="OOFEM"', xml)
            self.assertNotIn('section name="launch"', xml)
            self.assertIn('name="solver_executable" value=""', xml)
            self.assertIn('name="working_directory" value=""', xml)
            self.assertIn('name="results_directory" value=""', xml)
            self.assertIn('name="solver_timeout" value="300"', xml)
            self.assertIn('name="auto_open_paravis" value="false"', xml)
            self.assertIn('value="SalomePyQtGUILight"', xml)
            self.assertIn('value="oofem.png"', xml)
            self.assertTrue((resource / "oofem.png").is_file())
            self.assertTrue((resource / "oofem-logo.png").is_file())
            self.assertEqual(
                (resource / "oofem.png").read_bytes()[:8],
                b"\x89PNG\r\n\x1a\n",
            )
            self.assertEqual(
                struct.unpack(">II", (resource / "oofem.png").read_bytes()[16:24]),
                (48, 48),
            )
            self.assertEqual(
                struct.unpack(
                    ">II", (resource / "oofem-logo.png").read_bytes()[16:24]
                ),
                (400, 46),
            )
            self.assertEqual(
                (resource / "oofem.png").read_bytes(),
                (
                    module_root
                    / "bin"
                    / "salome"
                    / "OOFEMSalomePlugin"
                    / "resources"
                    / "icons"
                    / "oofem.png"
                ).read_bytes(),
            )

            extra_environment = salome_dir / "extra.env.d" / "oofem.py"
            self.assertEqual(
                extra_environment.read_text(encoding="utf-8"),
                MODULE_ENV.read_text(encoding="utf-8"),
            )

            self.assertEqual(
                salome_resource.read_text(encoding="utf-8"), original_resource
            )
            self.assertFalse(
                (salome_resource.parent / "SalomeApp.xml.before-oofem").exists()
            )
            launcher = salome_dir / "salome-oofem"
            launcher_text = launcher.read_text(encoding="utf-8")
            self.assertTrue(launcher.stat().st_mode & stat.S_IXUSR)
            self.assertIn('python3 "$user_config_registrar"', launcher_text)
            self.assertIn('exec "$salome_dir/salome" "$@"', launcher_text)
            self.assertNotIn(" -m ", launcher_text)

            user_xml = user_config.read_text(encoding="utf-8")
            self.assertIn('section name="OOFEM"', user_xml)
            self.assertIn('name="name" value="OOFEM"', user_xml)
            self.assertIn('name="icon" value="oofem.png"', user_xml)
            self.assertIn(
                'name="library" value="SalomePyQtGUILight"', user_xml
            )
            self.assertIn('name="geometry" value="800x600"', user_xml)
            self.assertIn(
                'name="solver_executable" value="/custom/oofem"', user_xml
            )
            self.assertIn('name="working_directory" value=""', user_xml)
            self.assertIn('name="results_directory" value=""', user_xml)
            self.assertIn('name="solver_timeout" value="300"', user_xml)
            self.assertIn('name="auto_open_paravis" value="false"', user_xml)
            self.assertIn(str(resource), user_xml)
            self.assertEqual(
                user_config.with_name(
                    "SalomeApprc.9.16.0.before-oofem"
                ).read_text(encoding="utf-8"),
                original_user_config,
            )

            legacy = subprocess.run(
                [
                    "bash",
                    str(MODULE_INSTALLER),
                    "--salome",
                    str(salome_dir),
                    "--legacy-global-registration",
                ],
                cwd=str(REPOSITORY_ROOT),
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertIn("Registered compatibility block", legacy.stdout)
            integrated_resource = salome_resource.read_text(encoding="utf-8")
            integration = MODULE_INTEGRATION.read_text(encoding="utf-8").strip()
            self.assertIn(integration, integrated_resource)
            self.assertEqual(
                integrated_resource.count("BEGIN OOFEM SALOME MODULE"), 1
            )
            self.assertEqual(
                (salome_resource.parent / "SalomeApp.xml.before-oofem").read_text(
                    encoding="utf-8"
                ),
                original_resource,
            )

    def test_installs_from_unpacked_native_layout_without_install_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            salome_dir = root / "SALOME-9.16.0-native-UB24.04-SRC"
            salome_dir.mkdir()
            salome_launcher = salome_dir / "salome"
            salome_launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            salome_launcher.chmod(0o755)
            version_file = (
                salome_dir
                / "BINARIES-UB24.04"
                / "GUI"
                / "bin"
                / "salome"
                / "VERSION"
            )
            version_file.parent.mkdir(parents=True)
            version_file.write_text("[SALOME GUI]  : 9.16.0\n", encoding="utf-8")
            self.assertFalse((salome_dir / "INSTALL").exists())

            environment = os.environ.copy()
            environment["XDG_CONFIG_HOME"] = str(root / "config")
            result = subprocess.run(
                ["bash", str(MODULE_INSTALLER), "--salome", str(salome_dir)],
                cwd=str(REPOSITORY_ROOT),
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            user_config = (
                pathlib.Path(environment["XDG_CONFIG_HOME"])
                / "salome"
                / "SalomeApprc.9.16.0"
            )
            self.assertIn("OOFEM SALOME module installed", result.stdout)
            self.assertTrue(user_config.is_file())
            self.assertIn(
                str(
                    salome_dir
                    / "INSTALL"
                    / "OOFEM"
                    / "share"
                    / "salome"
                    / "resources"
                    / "oofem"
                ),
                user_config.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
