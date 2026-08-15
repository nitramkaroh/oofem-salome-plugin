import pathlib
import subprocess
import tempfile
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = REPOSITORY_ROOT / "install.sh"
MARKER = "# >>> OOFEM SALOME plugin >>>"


class InstallerTests(unittest.TestCase):
    def run_installer(self, target):
        return subprocess.run(
            ["bash", str(INSTALLER), "--target", str(target)],
            cwd=str(REPOSITORY_ROOT),
            check=True,
            capture_output=True,
            text=True,
        )

    def test_installs_package_and_registration_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "plugins"

            first = self.run_installer(target)
            second = self.run_installer(target)

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

            self.run_installer(target)

            registration = registration_file.read_text(encoding="utf-8")
            self.assertIn("EXISTING_PLUGIN = True", registration)
            self.assertEqual(registration.count(MARKER), 1)


if __name__ == "__main__":
    unittest.main()
