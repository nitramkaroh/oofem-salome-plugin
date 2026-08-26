import json
import os
import pathlib
import sys
import tempfile
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from OOFEMSalomePlugin.OOFEMResults import (  # noqa: E402
    discover_run_files,
    read_pvd_time_steps,
    read_vtu_fields,
    summarize_run,
)


VTU = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid">
  <UnstructuredGrid><Piece>
    <PointData><DataArray Name="Displacement"/></PointData>
    <CellData><DataArray Name="Stress"/><DataArray Name="Material"/></CellData>
  </Piece></UnstructuredGrid>
</VTKFile>
"""


class OOFEMResultModelTests(unittest.TestCase):
    def test_summarizes_time_steps_fields_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            first = root / "model.out.m0.1.vtu"
            second = root / "model.out.m0.2.vtu"
            first.write_text(VTU, encoding="utf-8")
            second.write_text(VTU, encoding="utf-8")
            pvd = root / "model.out.m0.pvd"
            pvd.write_text(
                """<VTKFile><Collection>
                <DataSet timestep="0.25" file="model.out.m0.1.vtu"/>
                <DataSet timestep="1.0" file="model.out.m0.2.vtu"/>
                </Collection></VTKFile>""",
                encoding="utf-8",
            )
            (root / "model.in").write_text("model.out\n", encoding="utf-8")
            (root / "model.out").write_text("0 error(s)\n", encoding="utf-8")
            mesh = root / "model.msh"
            mesh.write_text("$MeshFormat\n", encoding="utf-8")
            manifest_file = root / "run.json"
            manifest_file.write_text(
                json.dumps({"run_id": "run-1", "status": "succeeded"}),
                encoding="utf-8",
            )

            summary = summarize_run(str(root))

            self.assertTrue(summary["exists"])
            self.assertEqual(summary["manifest"]["status"], "succeeded")
            self.assertEqual(summary["visualization_file"], str(pvd))
            self.assertEqual(
                [step["time"] for step in summary["time_steps"]], [0.25, 1.0]
            )
            self.assertEqual(summary["fields"]["point"], ["Displacement"])
            self.assertEqual(summary["fields"]["cell"], ["Stress", "Material"])
            self.assertIn(str(mesh), summary["files"])
            self.assertIn(str(manifest_file), summary["files"])

    def test_invalid_xml_and_missing_directory_are_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            broken = root / "broken.pvd"
            broken.write_text("<not-finished", encoding="utf-8")
            self.assertEqual(read_pvd_time_steps(str(broken), str(root)), [])
            self.assertEqual(
                read_vtu_fields(str(broken), str(root)),
                {"point": [], "cell": []},
            )
            self.assertFalse(summarize_run(str(root / "missing"))["exists"])

    def test_escaping_pvd_references_and_symlinks_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = pathlib.Path(directory)
            external = pathlib.Path(outside) / "outside.vtu"
            external.write_text(VTU, encoding="utf-8")
            pvd = root / "model.pvd"
            pvd.write_text(
                '<VTKFile><Collection><DataSet timestep="0" file="../outside.vtu"/>'
                '</Collection></VTKFile>',
                encoding="utf-8",
            )
            link = root / "linked.vtu"
            try:
                link.symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")

            self.assertEqual(read_pvd_time_steps(str(pvd), str(root)), [])
            self.assertNotIn(str(link), discover_run_files(str(root)))

    def test_manifest_must_be_an_object(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "manifest.json").write_text("[]", encoding="utf-8")
            self.assertEqual(summarize_run(str(root))["manifest"], {})


if __name__ == "__main__":
    unittest.main()
