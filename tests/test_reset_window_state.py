"""Tests for the remembered-window-layout cleanup tool."""

import importlib.util
import pathlib
import sys
import tempfile
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL_PATH = REPOSITORY_ROOT / "tools" / "reset_salome_window_state.py"

_spec = importlib.util.spec_from_file_location("reset_salome_window_state", TOOL_PATH)
reset_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reset_tool)


SAMPLE_CONFIG = """<!DOCTYPE document>
<document>
 <section name="OOFEM">
  <parameter name="name" value="OOFEM"/>
  <parameter name="icon" value="oofem.png"/>
  <parameter name="library" value="SalomePyQtGUILight"/>
  <parameter name="solver_timeout" value="300"/>
 </section>
 <section name="resources">
  <parameter name="OOFEM" value="/opt/oofem/resources/oofem"/>
 </section>
 <section name="windows_visibility">
  <parameter name="nomodule" value="@ByteArray[#00]"/>
  <parameter name="OOFEM" value="@ByteArray[#01]"/>
  <parameter name="" value="@ByteArray[#02]"/>
  <parameter name="SMESH" value="@ByteArray[#03]"/>
 </section>
 <section name="windows_geometry">
  <parameter name="OOFEM" value="@ByteArray[#04]"/>
 </section>
 <section name="windows_geometry_version">
  <parameter name="OOFEM" value="9.16.0"/>
 </section>
</document>
"""


class StripLayoutEntriesTests(unittest.TestCase):
    def test_removes_only_oofem_layout_keys(self):
        kept, removed = reset_tool.strip_layout_entries(
            SAMPLE_CONFIG.splitlines(keepends=True)
        )
        text = "".join(kept)

        self.assertEqual(
            sorted(removed),
            sorted(
                [
                    ("windows_visibility", "nomodule"),
                    ("windows_visibility", "OOFEM"),
                    ("windows_visibility", ""),
                    ("windows_geometry", "OOFEM"),
                    ("windows_geometry_version", "OOFEM"),
                ]
            ),
        )
        # Another module's remembered layout is none of our business.
        self.assertIn('<parameter name="SMESH" value="@ByteArray[#03]"/>', text)

    def test_preserves_the_module_registration_and_preferences(self):
        kept, _ = reset_tool.strip_layout_entries(
            SAMPLE_CONFIG.splitlines(keepends=True)
        )
        text = "".join(kept)

        # Removing these would un-register the module: no toolbar icon at all.
        self.assertIn('<section name="OOFEM">', text)
        self.assertIn('<parameter name="name" value="OOFEM"/>', text)
        self.assertIn('<parameter name="icon" value="oofem.png"/>', text)
        self.assertIn('value="SalomePyQtGUILight"', text)
        self.assertIn('<parameter name="solver_timeout" value="300"/>', text)
        self.assertIn('<parameter name="OOFEM" value="/opt/oofem/resources/oofem"/>', text)

    def test_every_section_header_survives(self):
        kept, _ = reset_tool.strip_layout_entries(
            SAMPLE_CONFIG.splitlines(keepends=True)
        )
        text = "".join(kept)
        for section in (
            "OOFEM",
            "resources",
            "windows_visibility",
            "windows_geometry",
            "windows_geometry_version",
        ):
            self.assertIn('<section name="{}">'.format(section), text)


class MainTests(unittest.TestCase):
    def _config(self, directory):
        path = pathlib.Path(directory) / "SalomeApprc.9.16.0"
        path.write_text(SAMPLE_CONFIG, encoding="utf-8")
        return path

    def test_dry_run_leaves_the_file_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory)
            with unittest.mock.patch.object(
                reset_tool, "salome_is_running", return_value=False
            ):
                self.assertEqual(
                    reset_tool.main(["--config", str(path), "--dry-run"]), 0
                )
            self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE_CONFIG)
            self.assertFalse(path.with_suffix(path.suffix + ".before-window-reset").exists())

    def test_write_backs_up_once_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory)
            backup = pathlib.Path(str(path) + ".before-window-reset")
            with unittest.mock.patch.object(
                reset_tool, "salome_is_running", return_value=False
            ):
                self.assertEqual(reset_tool.main(["--config", str(path)]), 0)
                self.assertTrue(backup.is_file())
                self.assertEqual(backup.read_text(encoding="utf-8"), SAMPLE_CONFIG)
                first = path.read_text(encoding="utf-8")

                # Re-running finds nothing left and must not clobber the backup.
                self.assertEqual(reset_tool.main(["--config", str(path)]), 0)
                self.assertEqual(path.read_text(encoding="utf-8"), first)
                self.assertEqual(backup.read_text(encoding="utf-8"), SAMPLE_CONFIG)

    def test_refuses_to_write_while_salome_is_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory)
            with unittest.mock.patch.object(
                reset_tool, "salome_is_running", return_value=True
            ):
                with self.assertRaises(SystemExit):
                    reset_tool.main(["--config", str(path)])
            self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE_CONFIG)

    def test_force_overrides_the_running_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory)
            with unittest.mock.patch.object(
                reset_tool, "salome_is_running", return_value=True
            ):
                self.assertEqual(
                    reset_tool.main(["--config", str(path), "--force"]), 0
                )
            self.assertNotIn("@ByteArray[#01]", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
