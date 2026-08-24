"""Atomic-write and Ogden-term regression tests for OOFEMExporter."""

import copy
import importlib
import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from tests.test_exporter import (  # noqa: E402
    OOFEMExporter,
    OOFEMValidationError,
    boundary_templates,
    ogden_quad_model,
    truss_model,
)


exporter_module = importlib.import_module("OOFEMSalomePlugin.OOFEMExporter")


def _truss_exporter():
    return OOFEMExporter(
        *truss_model(),
        boundary_templates(),
        solver_settings={"vtk": False},
    )


def _ogden_exporter(parameter_updates):
    mesh, mapping, materials, bcs = ogden_quad_model()
    materials = copy.deepcopy(materials)
    materials[0]["params"].update(parameter_updates)
    return OOFEMExporter(
        mesh,
        mapping,
        materials,
        copy.deepcopy(bcs),
        boundary_templates(),
        solver_settings={"vtk": False, "nlgeom": True},
    )


class AtomicExporterTests(unittest.TestCase):
    def test_success_renders_in_target_directory_then_uses_os_replace(self):
        exporter = _truss_exporter()
        replace_calls = []
        real_replace = os.replace

        def tracking_replace(source, destination):
            replace_calls.append((source, destination))
            return real_replace(source, destination)

        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "model.in"
            target.write_text("previous complete input\n", encoding="utf-8")
            with unittest.mock.patch.object(
                exporter_module.os, "replace", side_effect=tracking_replace
            ):
                result = exporter.export(str(target))

            self.assertEqual(result["input_file"], str(target.resolve()))
            self.assertEqual(len(replace_calls), 1)
            temporary_name, destination = replace_calls[0]
            self.assertEqual(pathlib.Path(temporary_name).parent, target.parent)
            self.assertEqual(pathlib.Path(destination), target.resolve())
            self.assertFalse(pathlib.Path(temporary_name).exists())
            self.assertNotEqual(
                target.read_text(encoding="utf-8"), "previous complete input\n"
            )
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_writer_failure_preserves_target_and_removes_temporary_file(self):
        exporter = _truss_exporter()
        sentinel = "known-good input remains intact\n"

        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "model.in"
            target.write_text(sentinel, encoding="utf-8")
            with unittest.mock.patch.object(
                exporter,
                "_export_time_functions",
                side_effect=RuntimeError("simulated writer failure"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "simulated writer failure"
                ):
                    exporter.export(str(target))

            self.assertEqual(target.read_text(encoding="utf-8"), sentinel)
            self.assertEqual(list(target.parent.iterdir()), [target])
            self.assertIsNone(exporter.last_result)

    def test_writer_failure_leaves_no_target_when_none_existed(self):
        exporter = _truss_exporter()

        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "new-model.in"
            with unittest.mock.patch.object(
                exporter,
                "_export_materials",
                side_effect=RuntimeError("material writer failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "material writer failed"):
                    exporter.export(str(target))

            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.iterdir()), [])


    def test_results_directory_controls_native_oofem_output_path(self):
        exporter = _truss_exporter()

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            input_directory = root / "inputs"
            results_directory = root / "results"
            input_directory.mkdir()
            exporter.solver_settings["output_directory"] = str(results_directory)
            target = input_directory / "model.in"

            result = exporter.export(str(target))

            expected_output = results_directory / "model.out"
            self.assertEqual(result["output_file"], str(expected_output))
            self.assertEqual(
                target.read_text(encoding="utf-8").splitlines()[0],
                str(expected_output),
            )
            self.assertTrue(results_directory.is_dir())


class OgdenOptionalPairTests(unittest.TestCase):
    def test_zero_optional_catalog_defaults_are_not_exported_as_terms(self):
        exporter = _ogden_exporter(
            {
                "alpha2": 0.0,
                "mu2": 0.0,
                "alpha3": 0.0,
                "mu3": 0.0,
            }
        )
        summary = exporter.validate()
        self.assertEqual(summary["materials"], 1)

        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "ogden.in"
            exporter.export(str(target))
            material_record = next(
                line
                for line in target.read_text(encoding="utf-8").splitlines()
                if line.startswith("ogdencompressiblemat ")
            )

        self.assertEqual(
            material_record,
            "ogdencompressiblemat 1 d 0 k 0 alpha 1 2 mu 1 20",
        )

    def test_incomplete_optional_pairs_are_rejected_during_validation(self):
        invalid_updates = [
            {"alpha2": 2.0},
            {"mu2": 5.0},
            {"alpha2": 0.0, "mu2": 5.0},
            {"alpha2": 2.0, "mu2": 0.0},
        ]

        for updates in invalid_updates:
            with self.subTest(updates=updates):
                exporter = _ogden_exporter(updates)
                with self.assertRaisesRegex(
                    OOFEMValidationError, "incomplete Ogden pair 2"
                ):
                    exporter.validate()

    def test_complete_optional_pair_is_exported_after_first_term(self):
        exporter = _ogden_exporter({"alpha2": -2.0, "mu2": 4.0})

        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "two-term-ogden.in"
            exporter.export(str(target))
            material_record = next(
                line
                for line in target.read_text(encoding="utf-8").splitlines()
                if line.startswith("ogdencompressiblemat ")
            )

        self.assertEqual(
            material_record,
            "ogdencompressiblemat 1 d 0 k 0 alpha 2 2 -2 mu 2 20 4",
        )


if __name__ == "__main__":
    unittest.main()
