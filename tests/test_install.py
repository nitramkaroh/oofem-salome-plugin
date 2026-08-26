import pathlib
import subprocess
import tempfile
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = REPOSITORY_ROOT / "install.sh"
MARKER = "# >>> OOFEM SALOME plugin >>>"


class InstallerTests(unittest.TestCase):
    def run_installer(self, *arguments, check=True):
        return subprocess.run(
            ["bash", str(INSTALLER), *arguments],
            cwd=str(REPOSITORY_ROOT),
            check=check,
            capture_output=True,
            text=True,
        )

    def install_legacy(self, target):
        return self.run_installer("--legacy-tools-plugin", "--target", str(target))

    def uninstall(self, target):
        return self.run_installer("--uninstall", "--target", str(target))

    def test_refuses_to_install_the_legacy_entry_without_an_explicit_flag(self):
        # The Tools > Plugins entry loads a second copy of the package that
        # can shadow the native module, so it must never appear by accident
        # -- e.g. from someone running the installer out of muscle memory.
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "plugins"

            result = self.run_installer("--target", str(target), check=False)

            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing to install", result.stderr)
            self.assertIn("install-salome-module.sh", result.stderr)
            self.assertFalse((target / "salome_plugins.py").exists())
            self.assertFalse((target / "OOFEMSalomePlugin").exists())

    def test_installs_package_and_registration_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "plugins"

            first = self.install_legacy(target)
            second = self.install_legacy(target)

            self.assertIn("Tools > Plugins > OOFEM", first.stdout)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0)
            self.assertTrue((target / "OOFEMSalomePlugin" / "__init__.py").is_file())
            self.assertTrue(
                (target / "OOFEMSalomePlugin" / "OOFEMSolverPresets.json").is_file()
            )
            self.assertTrue(
                (target / "OOFEMSalomePlugin" / "OOFEMRunner.py").is_file()
            )
            self.assertTrue(
                (target / "OOFEMSalomePlugin" / "OOFEMPost.py").is_file()
            )
            registration = (target / "salome_plugins.py").read_text(encoding="utf-8")
            self.assertEqual(registration.count(MARKER), 1)
            self.assertEqual(registration.count("_register_oofem_plugin()"), 1)

    def test_preserves_existing_user_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "plugins"
            target.mkdir(parents=True)
            registration_file = target / "salome_plugins.py"
            registration_file.write_text("EXISTING_PLUGIN = True\n", encoding="utf-8")

            self.install_legacy(target)

            registration = registration_file.read_text(encoding="utf-8")
            self.assertIn("EXISTING_PLUGIN = True", registration)
            self.assertEqual(registration.count(MARKER), 1)

    def test_uninstall_removes_the_package_and_the_whole_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "plugins"
            self.install_legacy(target)

            result = self.uninstall(target)

            self.assertEqual(result.returncode, 0)
            self.assertFalse((target / "OOFEMSalomePlugin").exists())
            # Nothing but OOFEM was in it, so the file itself goes away.
            self.assertFalse((target / "salome_plugins.py").exists())

    def test_uninstall_keeps_another_plugins_registration_in_a_shared_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "plugins"
            target.mkdir(parents=True)
            registration_file = target / "salome_plugins.py"
            registration_file.write_text("OTHER_PLUGIN = True\n", encoding="utf-8")
            self.install_legacy(target)

            self.uninstall(target)

            registration = registration_file.read_text(encoding="utf-8")
            self.assertIn("OTHER_PLUGIN = True", registration)
            self.assertNotIn(MARKER, registration)
            self.assertNotIn("_register_oofem_plugin", registration)

    def test_uninstall_is_idempotent_and_safe_on_a_clean_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "never-installed"

            first = self.uninstall(target)
            second = self.uninstall(target)

            self.assertEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0)
            self.assertIn("Nothing to remove", first.stdout)


if __name__ == "__main__":
    unittest.main()
