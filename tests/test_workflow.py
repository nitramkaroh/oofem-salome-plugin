import os
import pathlib
import stat
import sys
import tempfile
import types
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from OOFEMSalomePlugin.OOFEMConfig import (  # noqa: E402
    load_analysis_templates,
    load_material_catalog,
    load_solver_presets,
    solver_settings,
)
from OOFEMSalomePlugin.OOFEMPost import (  # noqa: E402
    convert_vtk_to_med,
    discover_result_files,
    open_in_paravis,
    preferred_visualization_file,
)
from OOFEMSalomePlugin.OOFEMRunner import (  # noqa: E402
    resolve_executable,
    run_solver,
)


class ConfigurationTests(unittest.TestCase):
    def test_catalog_contains_supported_templates_and_named_library(self):
        templates, library = load_material_catalog()
        self.assertEqual(
            {item["oofem_name"] for item in templates},
            {
                "Truss",
                "ElasticIsotropic2d",
                "ElasticIsotropic3d",
                "idm1",
                "misesmat",
                "ogdencompressiblemat",
                "mooneyrivlincompressiblemat",
            },
        )
        self.assertGreaterEqual(len(library), 2)
        self.assertTrue(all(item.get("params") for item in library))
        self.assertTrue(all(item.get("compatible_templates") for item in library))

    def test_solver_presets_include_vtk_and_text_modes(self):
        # The catalogue is a pure output-form choice now: which fields (if
        # any) OOFEM writes as VTK. The engineering model and its numeric
        # solution controls live on the Analysis tab; nlgeo is set per
        # cross section.
        presets = load_solver_presets()
        self.assertTrue(any(item["vtk"] for item in presets))
        self.assertTrue(any(not item["vtk"] for item in presets))
        self.assertTrue(solver_settings("vtk")["vtk"])
        self.assertFalse(solver_settings("text-only")["vtk"])
        contact = solver_settings("contact-vtk")
        for field_id in ("150", "151", "152"):
            self.assertIn(field_id, contact["vtk_record"])

    def test_output_form_catalogue_carries_no_engineering_model_or_solver_control_key(self):
        forbidden = {
            "engng_model",
            "nsteps",
            "nlgeom",
            "rtolv",
            "rtolf",
            "rtold",
            "maxiter",
            "miniter",
            "manrmsteps",
            "minsteplength",
            "initialguess",
            "smtype",
            "lstype",
            "stiffmode",
            "renumber",
            "controlmode",
            "refloadmode",
            "updateelasticstiffnessflag",
            "steplength",
            "initialsteplength",
            "psi",
            "reqiterations",
            "maxrestarts",
        }
        for preset in load_solver_presets():
            self.assertFalse(forbidden & set(preset), preset.get("id"))

    def test_arc_length_only_params_have_no_catalogue_default(self):
        # Direct control's own defaults (controlmode/stiffmode/rtolv/maxiter,
        # matching the retired "Nonlinear static, load control" preset) make
        # a freshly-selected Nonlinear Static analysis solve out of the box.
        # Arc-length-only keys must stay defaultless: a default here would
        # make every fresh Nonlinear Static project fail validation, since
        # the exporter rejects arc-length keys under direct control.
        templates = {
            item["oofem_name"]: item for item in load_analysis_templates()
        }
        parameters = {
            item["key"]: item
            for item in templates["nonlinearstatic"]["params"]
        }
        for key in ("controlmode", "stiffmode", "rtolv", "maxiter"):
            self.assertTrue(parameters[key]["optional"], key)
            self.assertIn("default", parameters[key])
        for key in (
            "steplength",
            "initialsteplength",
            "psi",
            "reqiterations",
            "maxrestarts",
        ):
            self.assertTrue(parameters[key]["optional"], key)
            self.assertNotIn("default", parameters[key])

    def test_analysis_catalog_exposes_nonlinear_static_controls(self):
        templates = {
            item["oofem_name"]: item for item in load_analysis_templates()
        }
        self.assertIn("nonlinearstatic", templates)
        parameters = {
            item["key"]: item
            for item in templates["nonlinearstatic"]["params"]
        }
        self.assertEqual(parameters["nsteps"]["default"], 10)
        # The Analysis tab is the only source of solution controls now, so
        # the common direct-control defaults are baked in...
        for key in ("controlmode", "stiffmode", "rtolv", "maxiter"):
            self.assertTrue(parameters[key]["optional"], key)
            self.assertIn("default", parameters[key])
            self.assertTrue(parameters[key]["description"])
        # ...while arc-length-only controls stay optional with no default.
        for key in ("steplength", "psi", "updateelasticstiffnessflag"):
            self.assertTrue(parameters[key]["optional"], key)
            self.assertNotIn("default", parameters[key])
            self.assertTrue(parameters[key]["description"])


class RunnerTests(unittest.TestCase):
    def test_resolves_configured_executable_and_runs_in_input_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            executable = directory / "fake-oofem"
            executable.write_text(
                "#!/bin/sh\nprintf 'working-directory=%s\\n' \"$PWD\"\nprintf '0 error(s)\\n'\n",
                encoding="utf-8",
            )
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            input_file = directory / "model.in"
            input_file.write_text("model.out\ntest\n", encoding="utf-8")

            self.assertEqual(resolve_executable(str(executable)), str(executable))
            result = run_solver(str(input_file), str(executable), timeout=5)
            self.assertTrue(result.succeeded)
            self.assertIn("working-directory={}".format(directory), result.stdout)


class PostprocessingTests(unittest.TestCase):
    def test_discovers_oofem_pvd_vtu_and_text_results_in_priority_order(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            input_file = directory / "model.in"
            output_file = directory / "custom.out"
            input_file.write_text("{}\ntest\n".format(output_file), encoding="utf-8")
            output_file.write_text("OOFEM text", encoding="utf-8")
            pvd = directory / "custom.out.m0.pvd"
            vtu = directory / "custom.out.m0.1.vtu"
            pvd.write_text("<VTKFile/>", encoding="utf-8")
            vtu.write_text("<VTKFile/>", encoding="utf-8")

            results = discover_result_files(str(input_file))
            self.assertEqual(results[0], str(pvd))
            self.assertIn(str(vtu), results)
            self.assertIn(str(output_file), results)
            self.assertEqual(preferred_visualization_file(results), str(pvd))

    def test_opens_result_through_lazy_paravis_api(self):
        calls = []
        fake_pvs = types.ModuleType("pvsimple")
        fake_pvs.OpenDataFile = lambda path: calls.append(("open", path)) or "source"
        fake_pvs.Show = lambda source: calls.append(("show", source))
        fake_pvs.ResetCamera = lambda: calls.append(("camera",))
        fake_pvs.Render = lambda: calls.append(("render",))
        previous = sys.modules.get("pvsimple")
        sys.modules["pvsimple"] = fake_pvs

        context = types.SimpleNamespace(
            sg=types.SimpleNamespace(
                activateModule=lambda name: calls.append(("activate", name))
            )
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / "result.vtu"
                path.write_text("<VTKFile/>", encoding="utf-8")
                source = open_in_paravis(str(path), context)
        finally:
            if previous is None:
                sys.modules.pop("pvsimple", None)
            else:
                sys.modules["pvsimple"] = previous

        self.assertEqual(source, "source")
        self.assertEqual(calls[0], ("activate", "ParaViS"))
        self.assertIn(("render",), calls)

    def test_med_conversion_is_delegated_to_optional_meshio(self):
        calls = []
        fake_meshio = types.ModuleType("meshio")
        fake_meshio.read = lambda path: calls.append(("read", path)) or "mesh"
        fake_meshio.write = lambda path, mesh: calls.append(("write", path, mesh))
        previous = sys.modules.get("meshio")
        sys.modules["meshio"] = fake_meshio
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = pathlib.Path(directory) / "result.vtu"
                destination = pathlib.Path(directory) / "result.med"
                source.write_text("<VTKFile/>", encoding="utf-8")
                converted = convert_vtk_to_med(str(source), str(destination))
        finally:
            if previous is None:
                sys.modules.pop("meshio", None)
            else:
                sys.modules["meshio"] = previous

        self.assertEqual(converted, str(destination))
        self.assertEqual(calls[-1], ("write", str(destination), "mesh"))


if __name__ == "__main__":
    unittest.main()
