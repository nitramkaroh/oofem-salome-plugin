"""Tests for reproducible, Qt-independent OOFEM run history."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile
import threading
import unittest
import unittest.mock


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import OOFEMSalomePlugin.OOFEMRunManager as run_module  # noqa: E402
from OOFEMSalomePlugin.OOFEMRunManager import (  # noqa: E402
    CANCELLED,
    FAILED,
    MANIFEST_FILE_NAME,
    OOFEMRunError,
    OOFEMRunManager,
    OOFEMRunManifestError,
    OOFEMRunStateError,
    PENDING,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
)


PROJECT = {
    "schema_version": 2,
    "analysis": {"oofem_type": "StaticStructural"},
}


class IncrementingClock:
    def __init__(self):
        self.value = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        value = self.value
        self.value += timedelta(seconds=1)
        return value


def _reserve_and_register(manager, name="model.in", content="result.out\nbody\n"):
    handle = manager.reserve_run(
        PROJECT,
        name,
        ["/opt/oofem", "-f", "{input}", "--cwd={run_dir}"],
        solver_version="OOFEM 2.6",
    )
    pathlib.Path(handle.input_file).write_text(content, encoding="utf-8")
    manager.register_input(handle.run_id)
    return handle


class OOFEMRunManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name) / "runs"
        self.manager = OOFEMRunManager(self.root, clock=IncrementingClock())

    def tearDown(self):
        self.temporary.cleanup()

    def test_reserve_register_and_qprocess_handle(self):
        project = {
            "schema_version": 2,
            "extension": {"values": [1, 2]},
        }
        handle = self.manager.reserve_run(
            project,
            "beam.in",
            ["/opt/oofem", "-f", "{input}", "--work={run_dir}"],
            solver_version="2.6-dev",
        )
        project["extension"]["values"].append(3)

        run_directory = pathlib.Path(handle.directory)
        self.assertTrue(run_directory.is_dir())
        self.assertEqual(pathlib.Path(handle.input_file).parent.name, "input")
        self.assertEqual(
            handle.results_directory, str(run_directory / "results")
        )
        self.assertEqual(handle.program, "/opt/oofem")
        self.assertEqual(
            handle.arguments,
            ["-f", "input/beam.in", "--work=."],
        )
        manifest = self.manager.load_run(handle.run_id)
        self.assertEqual(manifest["status"], PENDING)
        self.assertIsNone(manifest["input"]["registered_at"])
        self.assertEqual(
            manifest["project"]["state"]["extension"]["values"], [1, 2]
        )
        with self.assertRaises(OOFEMRunStateError):
            self.manager.mark_running(handle.run_id)

        input_bytes = b"beam.out\nline two\n"
        pathlib.Path(handle.input_file).write_bytes(input_bytes)
        prepared_handle = self.manager.prepare_run(handle.run_id)
        registered = self.manager.load_run(handle.run_id)
        self.assertEqual(prepared_handle, self.manager.get_handle(handle.run_id))
        self.assertEqual(
            registered["input"]["sha256"],
            hashlib.sha256(input_bytes).hexdigest(),
        )
        self.assertEqual(registered["input"]["size"], len(input_bytes))
        self.assertIsNotNone(registered["input"]["registered_at"])
        mode = stat.S_IMODE(pathlib.Path(handle.input_file).stat().st_mode)
        self.assertEqual(mode & 0o222, 0)

        # Registration is idempotent for the same frozen artifact.
        same = self.manager.register_input(handle.run_id)
        self.assertEqual(same, prepared_handle)
        second = self.manager.reserve_run(
            PROJECT, "beam.in", "/opt/oofem"
        )
        self.assertNotEqual(second.run_id, handle.run_id)
        self.assertNotEqual(second.directory, handle.directory)

    def test_running_success_log_and_result_provenance(self):
        handle = _reserve_and_register(self.manager)
        first_log = self.manager.write_solver_log(handle.run_id, "starting\n")
        self.assertEqual(first_log["solver_log"]["size"], len("starting\n"))
        self.manager.write_solver_log(handle.run_id, "starting\nfinished\n")

        running = self.manager.mark_running(handle.run_id, process_id=1234)
        self.assertEqual(running["status"], RUNNING)
        self.assertEqual(running["process_id"], 1234)
        result = pathlib.Path(handle.directory) / "results" / "model.vtu"
        result.write_bytes(b"<VTKFile />\n")

        completed = self.manager.finish_run(
            handle.run_id,
            0,
            result_files=["results/model.vtu"],
        )
        self.assertEqual(completed["status"], SUCCEEDED)
        self.assertEqual(completed["exit_code"], 0)
        self.assertIsNotNone(completed["started_at"])
        self.assertIsNotNone(completed["finished_at"])
        self.assertEqual(completed["result_files"][0]["path"], "results/model.vtu")
        self.assertEqual(
            completed["result_files"][0]["sha256"],
            hashlib.sha256(b"<VTKFile />\n").hexdigest(),
        )
        self.assertIsNotNone(completed["result_files"][0]["mtime"])
        self.assertEqual(
            self.manager.load_run(handle.run_id, verify_files=True)["status"],
            SUCCEEDED,
        )
        self.assertEqual(
            pathlib.Path(handle.directory, "solver.log").read_text(
                encoding="utf-8"
            ),
            "starting\nfinished\n",
        )
        self.assertEqual(
            pathlib.Path(handle.directory, "solver.log").stat().st_mode
            & stat.S_IWUSR,
            0,
        )
        self.assertEqual(
            pathlib.Path(handle.results_directory).stat().st_mode & 0o222,
            0,
        )
        with self.assertRaises(OOFEMRunStateError):
            self.manager.finish_run(handle.run_id, 0)
        with self.assertRaises(OOFEMRunStateError):
            self.manager.write_solver_log(handle.run_id, "too late")

    def test_failure_cancel_and_timeout_states(self):
        failed = _reserve_and_register(self.manager, "failed.in")
        self.manager.mark_running(failed.run_id)
        self.assertEqual(
            self.manager.finish_run(failed.run_id, 7, message="solver error")[
                "status"
            ],
            FAILED,
        )

        cancelled = _reserve_and_register(self.manager, "cancelled.in")
        self.manager.mark_running(cancelled.run_id)
        cancelled_manifest = self.manager.mark_cancelled(
            cancelled.run_id, exit_code=15
        )
        self.assertEqual(cancelled_manifest["status"], CANCELLED)
        self.assertEqual(cancelled_manifest["exit_code"], 15)

        timed_out = _reserve_and_register(self.manager, "timeout.in")
        self.manager.mark_running(timed_out.run_id)
        self.assertEqual(
            self.manager.mark_timed_out(timed_out.run_id)["status"],
            TIMED_OUT,
        )

        launch_failure = _reserve_and_register(self.manager, "launch.in")
        self.assertEqual(
            self.manager.mark_failed(
                launch_failure.run_id, message="could not start"
            )["status"],
            FAILED,
        )

    def test_large_result_skips_hash_and_external_result_is_rejected(self):
        manager = OOFEMRunManager(
            self.root / "large",
            clock=IncrementingClock(),
            result_hash_limit=1024,
        )
        handle = _reserve_and_register(manager)
        manager.mark_running(handle.run_id)
        large_result = pathlib.Path(handle.directory) / "results" / "large.vtu"
        with large_result.open("wb") as stream:
            stream.seek(2048)
            stream.write(b"x")

        real_hash = run_module._sha256_file

        def guarded_hash(path):
            if pathlib.Path(path) == large_result:
                raise AssertionError("large result must not be hashed")
            return real_hash(path)

        with unittest.mock.patch.object(
            run_module, "_sha256_file", side_effect=guarded_hash
        ):
            completed = manager.finish_run(
                handle.run_id, 0, result_files=[large_result]
            )
            restored_handle = manager.get_handle(handle.run_id)
        self.assertEqual(restored_handle.run_id, handle.run_id)
        entry = completed["result_files"][0]
        self.assertEqual(entry["size"], 2049)
        self.assertIsNone(entry["sha256"])
        self.assertEqual(entry["hash_skipped"], "size_limit")
        self.assertIsNotNone(entry["mtime"])

        other = _reserve_and_register(manager, "other.in")
        manager.mark_running(other.run_id)
        external = pathlib.Path(self.temporary.name) / "external.vtu"
        external.write_text("outside", encoding="utf-8")
        with self.assertRaisesRegex(OOFEMRunError, "inside the run directory"):
            manager.finish_run(other.run_id, 0, result_files=[external])
        self.assertEqual(manager.load_run(other.run_id)["status"], RUNNING)

    def test_result_hash_budget_is_bounded_across_all_artifacts(self):
        manager = OOFEMRunManager(
            self.root / "budget",
            clock=IncrementingClock(),
            result_hash_limit=10,
        )
        handle = _reserve_and_register(manager)
        manager.mark_running(handle.run_id)
        first = pathlib.Path(handle.results_directory) / "first.vtu"
        second = pathlib.Path(handle.results_directory) / "second.vtu"
        first.write_bytes(b"123456")
        second.write_bytes(b"abcdef")

        completed = manager.finish_run(
            handle.run_id, 0, result_files=[first, second]
        )

        self.assertIsNotNone(completed["result_files"][0]["sha256"])
        self.assertIsNone(completed["result_files"][1]["sha256"])
        self.assertEqual(
            completed["result_files"][1]["hash_skipped"], "size_limit"
        )

    def test_create_and_duplicate_preserve_source_and_rewrite_only_prefix(self):
        source = pathlib.Path(self.temporary.name) / "source.in"
        source_bytes = b"/old/output/model.out\r\nrecord one\r\nrecord two\r\n"
        source.write_bytes(source_bytes)
        original = self.manager.create_run(
            source,
            PROJECT,
            ["/opt/oofem", "-f", "{input}"],
            solver_version="2.6",
            rewrite_output_prefix=False,
        )
        self.assertEqual(pathlib.Path(original.input_file).read_bytes(), source_bytes)

        duplicate = self.manager.duplicate_run(original.run_id)
        duplicated_bytes = pathlib.Path(duplicate.input_file).read_bytes()
        first, remainder = duplicated_bytes.split(b"\r\n", 1)
        expected_output = (
            pathlib.Path(duplicate.directory) / "results" / "source.out"
        )
        self.assertEqual(os.fsdecode(first), str(expected_output.resolve()))
        self.assertEqual(remainder, source_bytes.split(b"\r\n", 1)[1])
        self.assertEqual(pathlib.Path(original.input_file).read_bytes(), source_bytes)
        duplicate_manifest = self.manager.load_run(
            duplicate.run_id, verify_files=True
        )
        self.assertEqual(duplicate_manifest["source_run_id"], original.run_id)
        self.assertEqual(
            duplicate_manifest["input"]["source_sha256"],
            self.manager.load_run(original.run_id)["input"]["sha256"],
        )
        self.assertNotEqual(
            duplicate_manifest["input"]["sha256"],
            self.manager.load_run(original.run_id)["input"]["sha256"],
        )

    def test_history_safe_loading_handle_lookup_and_delete(self):
        first = _reserve_and_register(self.manager, "one.in")
        self.manager.mark_cancelled(first.run_id)
        second = _reserve_and_register(self.manager, "two.in")

        history = self.manager.list_runs()
        self.assertEqual(
            [item["run_id"] for item in history],
            [second.run_id, first.run_id],
        )
        self.assertEqual(
            [item["run_id"] for item in self.manager.history(status=CANCELLED)],
            [first.run_id],
        )
        self.assertEqual(self.manager.list_runs(limit=1)[0]["run_id"], second.run_id)
        restored = self.manager.get_handle(first.run_id)
        self.assertEqual(restored.input_file, first.input_file)
        self.assertEqual(restored.directory, first.directory)

        corrupt_id = "run-20200101T000000000000Z-aaaaaaaaaaaa"
        corrupt_directory = self.root / corrupt_id
        corrupt_directory.mkdir()
        (corrupt_directory / MANIFEST_FILE_NAME).write_text(
            "{broken", encoding="utf-8"
        )
        self.assertNotIn(
            corrupt_id,
            [item["run_id"] for item in self.manager.list_runs()],
        )
        with self.assertRaises(OOFEMRunManifestError):
            self.manager.load_run("../outside")

        self.manager.mark_running(second.run_id)
        with self.assertRaises(OOFEMRunStateError):
            self.manager.delete_run(second.run_id)
        self.assertTrue(self.manager.delete_run(first.run_id))
        self.assertFalse(pathlib.Path(first.directory).exists())
        self.assertTrue(pathlib.Path(second.directory).exists())

    def test_manifest_update_is_atomic_and_preserves_previous_document(self):
        handle = _reserve_and_register(self.manager)
        manifest_path = pathlib.Path(handle.directory) / MANIFEST_FILE_NAME
        previous = manifest_path.read_bytes()
        with unittest.mock.patch.object(
            run_module.os,
            "replace",
            side_effect=OSError("simulated atomic replacement failure"),
        ):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.manager.mark_running(handle.run_id)

        self.assertEqual(manifest_path.read_bytes(), previous)
        self.assertEqual(self.manager.load_run(handle.run_id)["status"], PENDING)
        self.assertEqual(
            sorted(path.name for path in pathlib.Path(handle.directory).iterdir()),
            ["input", "results", MANIFEST_FILE_NAME],
        )

    def test_failed_terminal_manifest_update_restores_artifact_permissions(self):
        handle = _reserve_and_register(self.manager)
        self.manager.mark_running(handle.run_id)
        self.manager.write_solver_log(handle.run_id, "solver output\n")
        result = pathlib.Path(handle.results_directory) / "partial.vtu"
        result.write_text("<VTKFile/>\n", encoding="utf-8")
        manifest_path = pathlib.Path(handle.directory) / MANIFEST_FILE_NAME
        previous = manifest_path.read_bytes()

        with unittest.mock.patch.object(
            run_module.os,
            "replace",
            side_effect=OSError("simulated terminal replacement failure"),
        ):
            with self.assertRaisesRegex(OSError, "simulated terminal"):
                self.manager.finish_run(
                    handle.run_id,
                    0,
                    result_files=[str(result)],
                )

        self.assertEqual(manifest_path.read_bytes(), previous)
        self.assertEqual(self.manager.load_run(handle.run_id)["status"], RUNNING)
        self.assertNotEqual(result.stat().st_mode & 0o200, 0)
        self.assertNotEqual(
            pathlib.Path(handle.results_directory).stat().st_mode & 0o200, 0
        )
        self.assertNotEqual(
            pathlib.Path(handle.directory, "solver.log").stat().st_mode & 0o200,
            0,
        )

    def test_per_run_lock_prevents_second_terminal_writer_from_overwriting(self):
        handle = _reserve_and_register(self.manager)
        self.manager.mark_running(handle.run_id)
        second_manager = OOFEMRunManager(self.root)
        started = threading.Event()
        finished = threading.Event()
        errors = []

        def mark_failed_in_second_manager():
            started.set()
            try:
                second_manager.mark_failed(handle.run_id, message="recovered")
            except Exception as error:  # pragma: no cover - diagnostic capture
                errors.append(error)
            finally:
                finished.set()

        worker = threading.Thread(target=mark_failed_in_second_manager)
        with self.manager._run_lock(handle.run_id):
            worker.start()
            self.assertTrue(started.wait(1.0))
            self.assertFalse(finished.wait(0.05))

        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.manager.load_run(handle.run_id)["status"], FAILED)
        with self.assertRaises(OOFEMRunStateError):
            self.manager.finish_run(handle.run_id, 0)

    def test_manifest_path_traversal_is_rejected(self):
        handle = _reserve_and_register(self.manager)
        manifest_path = pathlib.Path(handle.directory) / MANIFEST_FILE_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["input"]["path"] = "../outside.in"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(OOFEMRunManifestError, "escapes"):
            self.manager.load_run(handle.run_id)


if __name__ == "__main__":
    unittest.main()
