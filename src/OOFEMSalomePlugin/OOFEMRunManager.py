"""Reproducible, study-independent OOFEM run history.

The manager has no Qt dependency. A GUI can reserve a unique directory,
export directly to handle.input_file, register the completed input, and
then connect QProcess signals to the lifecycle methods::

    handle = manager.reserve_run(project, "model.in", [oofem, "-f", "{input}"])
    exporter.export(handle.input_file)
    manager.register_input(handle.run_id)
    process.setWorkingDirectory(handle.working_directory)
    process.start(handle.program, handle.arguments)

Run directories are never reused. Registered inputs and terminal result
files are made read-only, while the manifest is replaced atomically for each
allowed lifecycle transition.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import uuid

try:  # POSIX SALOME builds
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    _fcntl = None

try:  # Native Windows plugin fallback
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    _msvcrt = None


_logger = logging.getLogger(__name__)

RUN_MANIFEST_FORMAT = "OOFEM reproducible run"
RUN_MANIFEST_VERSION = 1
MANIFEST_FILE_NAME = "run.json"
SOLVER_LOG_FILE_NAME = "solver.log"
INPUT_PLACEHOLDER = "{input}"
RUN_DIRECTORY_PLACEHOLDER = "{run_dir}"
# Hash at most this many result bytes in the synchronous completion path.
# Inputs and solver logs are always hashed; large result sets retain
# size/mtime provenance without freezing the SALOME GUI for an unbounded time.
DEFAULT_RESULT_HASH_LIMIT = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024

PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
TIMED_OUT = "timed_out"
RUN_STATUSES = frozenset(
    (PENDING, RUNNING, SUCCEEDED, FAILED, CANCELLED, TIMED_OUT)
)
TERMINAL_STATUSES = frozenset((SUCCEEDED, FAILED, CANCELLED, TIMED_OUT))

_RUN_ID_RE = re.compile(r"^run-\d{8}T\d{12}Z-[0-9a-f]{12}$")


class OOFEMRunError(RuntimeError):
    """Base class for run-history errors."""


class OOFEMRunNotFoundError(OOFEMRunError):
    """Raised when a validated run id has no directory."""


class OOFEMRunManifestError(OOFEMRunError):
    """Raised for corrupt, unsupported, or unsafe manifest data."""


class OOFEMRunStateError(OOFEMRunError):
    """Raised for an invalid or repeated lifecycle transition."""


@dataclass(frozen=True)
class OOFEMRunHandle:
    """Immutable paths and command needed to configure a QProcess."""

    run_id: str
    directory: str
    input_file: str
    command: tuple

    @property
    def working_directory(self):
        return self.directory

    @property
    def results_directory(self):
        return str(Path(self.directory) / "results")

    @property
    def program(self):
        return self.command[0]

    @property
    def arguments(self):
        return list(self.command[1:])


def _format_datetime(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _format_mtime(timestamp):
    return _format_datetime(datetime.fromtimestamp(timestamp, timezone.utc))


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _json_snapshot(value, description):
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return json.loads(payload)
    except (TypeError, ValueError) as error:
        raise OOFEMRunError(
            "{} must be JSON-serializable: {}".format(description, error)
        )


def _make_read_only(path):
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode & ~0o222)
    except OSError as error:
        raise OOFEMRunError(
            "Could not make run artifact read-only: {}".format(path)
        ) from error


def _freeze_directory_tree(root):
    """Make regular files and directories below *root* read-only."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise OOFEMRunError(
            "Run results directory is missing or unsafe: {}".format(root)
        )
    directories = []
    for current, names, filenames in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        directories.append(current_path)
        for name in names:
            candidate = current_path / name
            if not candidate.is_symlink():
                directories.append(candidate)
        for name in filenames:
            candidate = current_path / name
            if candidate.is_symlink():
                continue
            if not candidate.is_file():
                raise OOFEMRunError(
                    "Run result artifact is not a regular file: {}".format(
                        candidate
                    )
                )
            _make_read_only(candidate)
    for directory in sorted(
        set(directories), key=lambda path: len(path.parts), reverse=True
    ):
        _make_read_only(directory)


def _capture_tree_modes(root):
    """Capture modes for every non-symlink artifact touched by a freeze."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise OOFEMRunError(
            "Run results directory is missing or unsafe: {}".format(root)
        )
    modes = {root: stat.S_IMODE(root.stat().st_mode)}
    for current, names, filenames in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        modes.setdefault(
            current_path, stat.S_IMODE(current_path.stat().st_mode)
        )
        for name in names:
            candidate = current_path / name
            if candidate.is_symlink():
                continue
            if not candidate.is_dir():
                raise OOFEMRunError(
                    "Run result artifact is not a directory: {}".format(
                        candidate
                    )
                )
            modes[candidate] = stat.S_IMODE(candidate.stat().st_mode)
        for name in filenames:
            candidate = current_path / name
            if candidate.is_symlink():
                continue
            if not candidate.is_file():
                raise OOFEMRunError(
                    "Run result artifact is not a regular file: {}".format(
                        candidate
                    )
                )
            modes[candidate] = stat.S_IMODE(candidate.stat().st_mode)
    return modes


def _restore_modes(modes):
    """Best-effort rollback after a failed terminal manifest update."""
    for path, mode in sorted(
        modes.items(), key=lambda item: len(item[0].parts), reverse=True
    ):
        try:
            path.chmod(mode)
        except OSError as error:
            _logger.warning(
                "Could not restore run artifact permissions for %s: %s",
                path,
                error,
            )


def _locked_run(method):
    """Serialize one run mutation across manager instances/processes."""

    @wraps(method)
    def locked(self, run_id, *args, **kwargs):
        with self._run_lock(run_id):
            return method(self, run_id, *args, **kwargs)

    return locked


def _remove_read_only(function, path, unused_error):
    """Allow shutil.rmtree to remove frozen artifacts on Windows and POSIX."""
    del unused_error
    parent = os.path.dirname(path)
    if parent:
        parent_mode = stat.S_IMODE(os.lstat(parent).st_mode)
        os.chmod(parent, parent_mode | stat.S_IWUSR | stat.S_IXUSR)
    current_mode = stat.S_IMODE(os.lstat(path).st_mode)
    os.chmod(path, current_mode | stat.S_IWUSR)
    function(path)


def _rmtree(path):
    shutil.rmtree(str(path), onerror=_remove_read_only)


def _atomic_write_json(path, document):
    try:
        payload = json.dumps(
            document,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as error:
        raise OOFEMRunManifestError(
            "Run manifest is not JSON-serializable: {}".format(error)
        )

    temporary = path.with_name(
        ".{}.{}.tmp".format(path.name, uuid.uuid4().hex)
    )
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(str(path.parent), flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # os.replace already gives atomic visibility. Directory fsync is
            # an extra durability guarantee unavailable on some platforms.
            pass
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_text(path, text):
    temporary = path.with_name(
        ".{}.{}.tmp".format(path.name, uuid.uuid4().hex)
    )
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class OOFEMRunManager:
    """Create and maintain immutable OOFEM run snapshots."""

    def __init__(self, runs_root, clock=None, result_hash_limit=None):
        self.root = Path(runs_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise OOFEMRunError(
                "Run-history root is not a directory: {}".format(self.root)
            )
        self._locks_root = self.root / ".locks"
        if self._locks_root.is_symlink():
            raise OOFEMRunError("Run-history lock directory must not be a symlink")
        self._locks_root.mkdir(mode=0o700, exist_ok=True)
        if not self._locks_root.is_dir():
            raise OOFEMRunError("Run-history lock path is not a directory")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if result_hash_limit is None:
            result_hash_limit = DEFAULT_RESULT_HASH_LIMIT
        if (
            isinstance(result_hash_limit, bool)
            or not isinstance(result_hash_limit, int)
            or result_hash_limit < 0
        ):
            raise ValueError("result_hash_limit must be a non-negative integer")
        self.result_hash_limit = result_hash_limit

    def _now(self):
        value = self._clock()
        if not isinstance(value, datetime):
            raise OOFEMRunError("Run-manager clock must return datetime")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _timestamp(self):
        return _format_datetime(self._now())

    def _allocate_run_directory(self, now):
        time_part = now.strftime("%Y%m%dT%H%M%S%fZ")
        for unused in range(100):
            run_id = "run-{}-{}".format(time_part, uuid.uuid4().hex[:12])
            directory = self.root / run_id
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                continue
            return run_id, directory
        raise OOFEMRunError("Could not allocate a unique OOFEM run directory")

    def _validate_run_id(self, run_id):
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise OOFEMRunManifestError("Invalid OOFEM run id: {!r}".format(run_id))
        return run_id

    def _run_directory(self, run_id, require_exists=True):
        run_id = self._validate_run_id(run_id)
        directory = self.root / run_id
        if directory.parent != self.root or directory.name != run_id:
            raise OOFEMRunManifestError("Run path escapes the history root")
        if directory.is_symlink():
            raise OOFEMRunManifestError("Run directory must not be a symlink")
        if require_exists and not directory.is_dir():
            raise OOFEMRunNotFoundError("OOFEM run does not exist: {}".format(run_id))
        return directory

    @contextmanager
    def _run_lock(self, run_id):
        """Hold a persistent per-run OS lock until a state mutation commits."""
        run_id = self._validate_run_id(run_id)
        lock_path = self._locks_root / (run_id + ".lock")
        if lock_path.is_symlink():
            raise OOFEMRunError("Run lock file must not be a symlink")
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(str(lock_path), flags, 0o600)
        except OSError as error:
            raise OOFEMRunError(
                "Could not open run lock: {}".format(lock_path)
            ) from error

        locked = False
        try:
            if _fcntl is not None:
                _fcntl.flock(descriptor, _fcntl.LOCK_EX)
            elif _msvcrt is not None:  # pragma: no cover - Windows only
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                _msvcrt.locking(descriptor, _msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - unsupported Python platform
                raise OOFEMRunError(
                    "No supported interprocess file-lock API is available"
                )
            locked = True
            yield
        finally:
            try:
                if locked and _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_UN)
                elif locked and _msvcrt is not None:  # pragma: no cover
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)
            finally:
                os.close(descriptor)

    @staticmethod
    def _validate_input_name(input_name):
        try:
            input_name = os.fspath(input_name)
        except TypeError:
            raise OOFEMRunError("input_name must be a file name")
        if (
            not input_name
            or input_name in (".", "..")
            or Path(input_name).name != input_name
            or "\x00" in input_name
        ):
            raise OOFEMRunError(
                "input_name must be a plain file name, not a path"
            )
        return input_name

    @staticmethod
    def _normalise_command(command, input_path):
        if isinstance(command, (str, os.PathLike)):
            command = [os.fspath(command), "-f", INPUT_PLACEHOLDER]
        else:
            try:
                command = list(command)
            except TypeError:
                raise OOFEMRunError("solver_command must be a sequence")
        if not command:
            raise OOFEMRunError("solver_command must not be empty")
        if any(not isinstance(part, (str, os.PathLike)) for part in command):
            raise OOFEMRunError("solver_command entries must be strings")

        resolved = []
        found_input = False
        for part in command:
            part = os.fspath(part)
            if INPUT_PLACEHOLDER in part:
                found_input = True
            part = part.replace(INPUT_PLACEHOLDER, input_path)
            part = part.replace(RUN_DIRECTORY_PLACEHOLDER, ".")
            resolved.append(part)
        if not found_input:
            raise OOFEMRunError(
                "solver_command must contain the {!r} placeholder".format(
                    INPUT_PLACEHOLDER
                )
            )
        if not resolved[0]:
            raise OOFEMRunError("solver executable must not be empty")
        return resolved

    def _new_handle(self, directory, manifest):
        input_file = directory / manifest["input"]["path"]
        return OOFEMRunHandle(
            run_id=manifest["run_id"],
            directory=str(directory),
            input_file=str(input_file),
            command=tuple(manifest["solver"]["command"]),
        )

    def reserve_run(
        self,
        project_state,
        input_name,
        solver_command,
        solver_version=None,
        source_run_id=None,
    ):
        """Reserve a unique directory before the GUI exports its input file."""
        if not isinstance(project_state, dict):
            raise OOFEMRunError("project_state must be a dictionary")
        project_snapshot = _json_snapshot(project_state, "project_state")
        input_name = self._validate_input_name(input_name)
        if solver_version is not None and not isinstance(solver_version, str):
            raise OOFEMRunError("solver_version must be a string or None")
        if source_run_id is not None:
            self.load_run(source_run_id)

        relative_input = PurePosixPath("input", input_name).as_posix()
        command = self._normalise_command(solver_command, relative_input)
        now = self._now()
        timestamp = _format_datetime(now)
        run_id, directory = self._allocate_run_directory(now)

        try:
            (directory / "input").mkdir()
            (directory / "results").mkdir()
            manifest = {
                "format": RUN_MANIFEST_FORMAT,
                "version": RUN_MANIFEST_VERSION,
                "run_id": run_id,
                "source_run_id": source_run_id,
                "status": PENDING,
                "project": {
                    "schema_version": project_snapshot.get("schema_version"),
                    "state": project_snapshot,
                },
                "input": {
                    "path": relative_input,
                    "source": None,
                    "source_sha256": None,
                    "size": None,
                    "sha256": None,
                    "mtime": None,
                    "registered_at": None,
                },
                "solver": {
                    "command": command,
                    "version": solver_version,
                },
                "created_at": timestamp,
                "updated_at": timestamp,
                "started_at": None,
                "finished_at": None,
                "process_id": None,
                "exit_code": None,
                "message": None,
                "solver_log": None,
                "result_files": [],
            }
            _atomic_write_json(directory / MANIFEST_FILE_NAME, manifest)
            return self._new_handle(directory, manifest)
        except Exception:
            self._remove_new_directory(directory)
            raise

    def _remove_new_directory(self, directory):
        if directory.parent != self.root or not _RUN_ID_RE.fullmatch(directory.name):
            return
        try:
            _rmtree(directory)
        except FileNotFoundError:
            pass

    def _input_path(self, directory, manifest):
        return self._safe_manifest_path(
            directory, manifest["input"]["path"], "input"
        )

    @staticmethod
    def _safe_manifest_path(directory, relative_path, description):
        if not isinstance(relative_path, str) or not relative_path:
            raise OOFEMRunManifestError(
                "Manifest {} path is invalid".format(description)
            )
        # Manifests persist portable POSIX separators.  Accept legacy Windows
        # separators when histories are moved to another operating system.
        path = PurePosixPath(relative_path.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise OOFEMRunManifestError(
                "Manifest {} path escapes the run".format(description)
            )
        candidate = directory.joinpath(*path.parts)
        try:
            candidate.resolve().relative_to(directory.resolve())
        except (OSError, ValueError):
            raise OOFEMRunManifestError(
                "Manifest {} path escapes the run".format(description)
            )
        return candidate

    @_locked_run
    def _register_input(
        self,
        run_id,
        input_file=None,
        source=None,
        source_sha256=None,
    ):
        manifest = self.load_run(run_id)
        if manifest["status"] != PENDING:
            raise OOFEMRunStateError(
                "Input can only be registered while a run is pending"
            )
        directory = self._run_directory(run_id)
        expected = self._input_path(directory, manifest)
        supplied = expected if input_file is None else Path(input_file).expanduser()
        if expected.is_symlink() or supplied.is_symlink():
            raise OOFEMRunError("Registered input must not be a symlink")
        try:
            supplied = supplied.resolve(strict=True)
            expected_resolved = expected.resolve(strict=True)
        except OSError as error:
            raise OOFEMRunError(
                "OOFEM input does not exist: {}".format(supplied)
            ) from error
        if supplied != expected_resolved:
            raise OOFEMRunError(
                "Registered input must be the reserved file {}".format(expected)
            )
        if supplied.is_symlink() or not supplied.is_file():
            raise OOFEMRunError("Registered input must be a regular file")

        stat_result = supplied.stat()
        digest = _sha256_file(supplied)
        input_data = manifest["input"]
        if input_data["registered_at"] is not None:
            if (
                input_data["sha256"] == digest
                and input_data["size"] == stat_result.st_size
            ):
                return manifest
            raise OOFEMRunStateError("Registered input is immutable")

        _make_read_only(supplied)
        _make_read_only(supplied.parent)
        input_data.update(
            {
                "source": str(source) if source is not None else None,
                "source_sha256": source_sha256,
                "size": stat_result.st_size,
                "sha256": digest,
                "mtime": _format_mtime(stat_result.st_mtime),
                "registered_at": self._timestamp(),
            }
        )
        manifest["updated_at"] = input_data["registered_at"]
        _atomic_write_json(directory / MANIFEST_FILE_NAME, manifest)
        return manifest

    def register_input(self, run_id, input_file=None):
        """Hash and freeze the input exported into a reserved run directory."""
        manifest = self._register_input(run_id, input_file=input_file)
        return self._new_handle(self._run_directory(run_id), manifest)

    def prepare_run(self, run_id, input_file=None):
        """Alias suitable for GUI code that prepares a run before QProcess."""
        return self.register_input(run_id, input_file=input_file)

    def create_run(
        self,
        input_file,
        project_state,
        solver_command,
        solver_version=None,
        source_run_id=None,
        rewrite_output_prefix=True,
    ):
        """Reserve a run, copy an existing input, and register it."""
        source = Path(input_file).expanduser()
        if source.is_symlink():
            raise OOFEMRunError("OOFEM input must not be a symbolic link")
        try:
            source = source.resolve(strict=True)
        except OSError as error:
            raise OOFEMRunError(
                "OOFEM input does not exist: {}".format(input_file)
            ) from error
        if source.is_symlink() or not source.is_file():
            raise OOFEMRunError("OOFEM input must be a regular file")

        handle = self.reserve_run(
            project_state,
            source.name,
            solver_command,
            solver_version=solver_version,
            source_run_id=source_run_id,
        )
        destination = Path(handle.input_file)
        try:
            source_digest = _sha256_file(source)
            if rewrite_output_prefix:
                output_file = (
                    Path(handle.directory) / "results" / (source.stem + ".out")
                )
                self._copy_with_output_prefix(source, destination, output_file)
            else:
                shutil.copyfile(str(source), str(destination))
            self._register_input(
                handle.run_id,
                source=str(source),
                source_sha256=source_digest,
            )
            return handle
        except Exception:
            self._remove_new_directory(Path(handle.directory))
            raise

    @staticmethod
    def _copy_with_output_prefix(source, destination, output_file):
        """Copy text input while replacing only its first output-prefix line."""
        temporary = destination.with_name(
            ".{}.{}.tmp".format(destination.name, uuid.uuid4().hex)
        )
        try:
            with source.open("rb") as source_stream:
                first_line = source_stream.readline()
                if not first_line:
                    raise OOFEMRunError("Cannot rewrite an empty OOFEM input")
                if first_line.endswith(b"\r\n"):
                    newline = b"\r\n"
                elif first_line.endswith(b"\n"):
                    newline = b"\n"
                else:
                    newline = b"\n"
                with temporary.open("xb") as destination_stream:
                    destination_stream.write(
                        os.fsencode(str(output_file.resolve())) + newline
                    )
                    shutil.copyfileobj(source_stream, destination_stream)
                    destination_stream.flush()
                    os.fsync(destination_stream.fileno())
            os.replace(str(temporary), str(destination))
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def duplicate_run(
        self,
        source_run_id,
        solver_command=None,
        solver_version=None,
    ):
        """Create a rerun without modifying the source run or input."""
        source_manifest = self.load_run(source_run_id)
        if source_manifest["input"]["registered_at"] is None:
            raise OOFEMRunStateError("Source run has no registered input")
        source_directory = self._run_directory(source_run_id)
        self._verify_input_file(source_directory, source_manifest)
        source_input = self._input_path(source_directory, source_manifest)

        if solver_command is None:
            source_input_token = source_manifest["input"]["path"]
            solver_command = [
                part.replace(source_input_token, INPUT_PLACEHOLDER)
                for part in source_manifest["solver"]["command"]
            ]
        if solver_version is None:
            solver_version = source_manifest["solver"]["version"]

        handle = self.reserve_run(
            source_manifest["project"]["state"],
            source_input.name,
            solver_command,
            solver_version=solver_version,
            source_run_id=source_run_id,
        )
        destination = Path(handle.input_file)
        output_file = (
            Path(handle.directory)
            / "results"
            / (source_input.stem + ".out")
        )
        try:
            self._copy_with_output_prefix(
                source_input, destination, output_file
            )
            self._register_input(
                handle.run_id,
                source=str(source_input),
                source_sha256=source_manifest["input"]["sha256"],
            )
            return handle
        except Exception:
            self._remove_new_directory(Path(handle.directory))
            raise

    def _require_transition(self, manifest, target):
        current = manifest["status"]
        allowed = {
            PENDING: frozenset((RUNNING, FAILED, CANCELLED, TIMED_OUT)),
            RUNNING: TERMINAL_STATUSES,
        }.get(current, frozenset())
        if target not in allowed:
            raise OOFEMRunStateError(
                "Invalid run transition: {} -> {}".format(current, target)
            )

    @_locked_run
    def mark_running(self, run_id, process_id=None):
        """Record QProcess.started after a registered input is ready."""
        manifest = self.load_run(run_id)
        directory = self._run_directory(run_id)
        self._verify_input_file(directory, manifest)
        self._require_transition(manifest, RUNNING)
        if manifest["input"]["registered_at"] is None:
            raise OOFEMRunStateError(
                "Run input must be registered before the solver starts"
            )
        if process_id is not None and (
            isinstance(process_id, bool)
            or not isinstance(process_id, int)
            or process_id <= 0
        ):
            raise OOFEMRunError("process_id must be a positive integer or None")
        timestamp = self._timestamp()
        manifest.update(
            {
                "status": RUNNING,
                "started_at": timestamp,
                "updated_at": timestamp,
                "process_id": process_id,
            }
        )
        _atomic_write_json(directory / MANIFEST_FILE_NAME, manifest)
        return manifest

    def _result_entry(
        self, directory, result_file, hash_results, hash_limit=None
    ):
        path = Path(result_file).expanduser()
        if not path.is_absolute():
            path = directory / path
        if path.is_symlink():
            raise OOFEMRunError("Result artifact must not be a symlink")
        try:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(directory.resolve())
        except (OSError, ValueError) as error:
            raise OOFEMRunError(
                "Result file must exist inside the run directory: {}".format(path)
            ) from error
        if resolved.is_symlink() or not resolved.is_file():
            raise OOFEMRunError("Result artifact must be a regular file")
        stat_result = resolved.stat()
        digest = None
        hash_skipped = None
        if not hash_results:
            hash_skipped = "disabled"
        elif stat_result.st_size > (
            self.result_hash_limit if hash_limit is None else hash_limit
        ):
            hash_skipped = "size_limit"
        else:
            digest = _sha256_file(resolved)
        return {
            "path": PurePosixPath(*relative.parts).as_posix(),
            "size": stat_result.st_size,
            "mtime": _format_mtime(stat_result.st_mtime),
            "sha256": digest,
            "hash_skipped": hash_skipped,
        }

    @_locked_run
    def _complete(
        self,
        run_id,
        status,
        exit_code=None,
        result_files=(),
        message=None,
        hash_results=True,
    ):
        manifest = self.load_run(run_id)
        self._require_transition(manifest, status)
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            raise OOFEMRunError("exit_code must be an integer or None")
        if message is not None and not isinstance(message, str):
            raise OOFEMRunError("message must be a string or None")
        if isinstance(result_files, (str, os.PathLike)):
            result_files = [result_files]

        directory = self._run_directory(run_id)
        entries = []
        registered_result_paths = []
        seen_paths = set()
        remaining_hash_bytes = self.result_hash_limit
        for result_file in result_files:
            entry = self._result_entry(
                directory,
                result_file,
                hash_results,
                hash_limit=remaining_hash_bytes,
            )
            if entry["path"] not in seen_paths:
                entries.append(entry)
                registered_result_paths.append(
                    self._safe_manifest_path(
                        directory, entry["path"], "result artifact"
                    )
                )
                seen_paths.add(entry["path"])
                if entry["sha256"] is not None:
                    remaining_hash_bytes -= entry["size"]
        solver_log = manifest.get("solver_log")
        log_path = None
        if solver_log is not None:
            log_path = self._safe_manifest_path(
                directory, solver_log["path"], "solver log"
            )
            if log_path.is_symlink() or not log_path.is_file():
                raise OOFEMRunError("Solver log is missing or unsafe")

        timestamp = self._timestamp()
        manifest.update(
            {
                "status": status,
                "exit_code": exit_code,
                "finished_at": timestamp,
                "updated_at": timestamp,
                "message": message,
                "result_files": entries,
            }
        )
        permission_snapshot = _capture_tree_modes(directory / "results")
        for path in registered_result_paths:
            permission_snapshot.setdefault(
                path, stat.S_IMODE(path.stat().st_mode)
            )
        if log_path is not None:
            permission_snapshot.setdefault(
                log_path, stat.S_IMODE(log_path.stat().st_mode)
            )
        try:
            for path in registered_result_paths:
                _make_read_only(path)
            if log_path is not None:
                _make_read_only(log_path)
            _freeze_directory_tree(directory / "results")
            _atomic_write_json(directory / MANIFEST_FILE_NAME, manifest)
        except Exception:
            _restore_modes(permission_snapshot)
            raise
        return manifest

    def finish_run(
        self,
        run_id,
        exit_code,
        result_files=(),
        succeeded=None,
        message=None,
        hash_results=True,
    ):
        """Finish a QProcess run; exit code zero succeeds unless overridden."""
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise OOFEMRunError("exit_code must be an integer")
        if succeeded is None:
            succeeded = exit_code == 0
        if not isinstance(succeeded, bool):
            raise OOFEMRunError("succeeded must be bool or None")
        status = SUCCEEDED if succeeded else FAILED
        return self._complete(
            run_id,
            status,
            exit_code=exit_code,
            result_files=result_files,
            message=message,
            hash_results=hash_results,
        )

    def mark_failed(
        self,
        run_id,
        exit_code=None,
        result_files=(),
        message=None,
        hash_results=True,
    ):
        return self._complete(
            run_id,
            FAILED,
            exit_code=exit_code,
            result_files=result_files,
            message=message,
            hash_results=hash_results,
        )

    def mark_cancelled(
        self,
        run_id,
        exit_code=None,
        result_files=(),
        message=None,
        hash_results=True,
    ):
        return self._complete(
            run_id,
            CANCELLED,
            exit_code=exit_code,
            result_files=result_files,
            message=message,
            hash_results=hash_results,
        )

    def mark_timed_out(
        self,
        run_id,
        exit_code=None,
        result_files=(),
        message=None,
        hash_results=True,
    ):
        return self._complete(
            run_id,
            TIMED_OUT,
            exit_code=exit_code,
            result_files=result_files,
            message=message,
            hash_results=hash_results,
        )

    @_locked_run
    def write_solver_log(self, run_id, text):
        """Atomically replace the accumulated solver log before completion."""
        if not isinstance(text, str):
            raise OOFEMRunError("Solver log must be text")
        manifest = self.load_run(run_id)
        if manifest["status"] in TERMINAL_STATUSES:
            raise OOFEMRunStateError(
                "Solver log cannot be changed after run completion"
            )
        directory = self._run_directory(run_id)
        log_path = directory / SOLVER_LOG_FILE_NAME
        _atomic_write_text(log_path, text)
        stat_result = log_path.stat()
        timestamp = self._timestamp()
        manifest["solver_log"] = {
            "path": SOLVER_LOG_FILE_NAME,
            "size": stat_result.st_size,
            "mtime": _format_mtime(stat_result.st_mtime),
            "sha256": _sha256_file(log_path),
        }
        manifest["updated_at"] = timestamp
        _atomic_write_json(directory / MANIFEST_FILE_NAME, manifest)
        return manifest

    def _validate_manifest(self, directory, manifest):
        if not isinstance(manifest, dict):
            raise OOFEMRunManifestError("Run manifest must be a JSON object")
        if manifest.get("format") != RUN_MANIFEST_FORMAT:
            raise OOFEMRunManifestError("Not an OOFEM run manifest")
        if manifest.get("version") != RUN_MANIFEST_VERSION:
            raise OOFEMRunManifestError(
                "Unsupported OOFEM run manifest version: {!r}".format(
                    manifest.get("version")
                )
            )
        if manifest.get("run_id") != directory.name:
            raise OOFEMRunManifestError(
                "Run manifest id does not match its directory"
            )
        if manifest.get("status") not in RUN_STATUSES:
            raise OOFEMRunManifestError("Run manifest has an invalid status")

        project = manifest.get("project")
        if not isinstance(project, dict) or not isinstance(
            project.get("state"), dict
        ):
            raise OOFEMRunManifestError("Run manifest has invalid project data")
        input_data = manifest.get("input")
        if not isinstance(input_data, dict):
            raise OOFEMRunManifestError("Run manifest has invalid input data")
        self._safe_manifest_path(
            directory, input_data.get("path"), "input"
        )
        registered_at = input_data.get("registered_at")
        if registered_at is not None and not isinstance(registered_at, str):
            raise OOFEMRunManifestError(
                "Run manifest has invalid input registration time"
            )
        if registered_at is not None:
            if (
                isinstance(input_data.get("size"), bool)
                or not isinstance(input_data.get("size"), int)
                or input_data.get("size") < 0
                or not _is_sha256(input_data.get("sha256"))
                or not isinstance(input_data.get("mtime"), str)
            ):
                raise OOFEMRunManifestError(
                    "Run manifest has invalid registered input metadata"
                )
        if input_data.get("source") is not None and not isinstance(
            input_data.get("source"), str
        ):
            raise OOFEMRunManifestError(
                "Run manifest has invalid input source"
            )
        if input_data.get("source_sha256") is not None and not _is_sha256(
            input_data.get("source_sha256")
        ):
            raise OOFEMRunManifestError(
                "Run manifest has invalid source input checksum"
            )
        solver = manifest.get("solver")
        if not isinstance(solver, dict):
            raise OOFEMRunManifestError("Run manifest has invalid solver data")
        command = solver.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) for part in command)
        ):
            raise OOFEMRunManifestError("Run manifest has invalid solver command")
        if solver.get("version") is not None and not isinstance(
            solver.get("version"), str
        ):
            raise OOFEMRunManifestError("Run manifest has invalid solver version")

        for field in (
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
        ):
            value = manifest.get(field)
            if value is not None and not isinstance(value, str):
                raise OOFEMRunManifestError(
                    "Run manifest has invalid {}".format(field)
                )
        if not isinstance(manifest.get("created_at"), str) or not isinstance(
            manifest.get("updated_at"), str
        ):
            raise OOFEMRunManifestError(
                "Run manifest is missing required timestamps"
            )
        exit_code = manifest.get("exit_code")
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            raise OOFEMRunManifestError("Run manifest has invalid exit code")
        source_run_id = manifest.get("source_run_id")
        if source_run_id is not None:
            self._validate_run_id(source_run_id)

        result_files = manifest.get("result_files")
        if not isinstance(result_files, list):
            raise OOFEMRunManifestError("Run manifest has invalid result files")
        for entry in result_files:
            if not isinstance(entry, dict):
                raise OOFEMRunManifestError(
                    "Run manifest has invalid result entry"
                )
            self._safe_manifest_path(
                directory, entry.get("path"), "result"
            )
            if (
                isinstance(entry.get("size"), bool)
                or not isinstance(entry.get("size"), int)
                or entry.get("size") < 0
            ):
                raise OOFEMRunManifestError(
                    "Run manifest has invalid result size"
                )
            if entry.get("sha256") is not None and not isinstance(
                entry.get("sha256"), str
            ):
                raise OOFEMRunManifestError(
                    "Run manifest has invalid result checksum"
                )
            if entry.get("sha256") is not None and not _is_sha256(
                entry.get("sha256")
            ):
                raise OOFEMRunManifestError(
                    "Run manifest has malformed result checksum"
                )
            if not isinstance(entry.get("mtime"), str):
                raise OOFEMRunManifestError(
                    "Run manifest has invalid result mtime"
                )
        solver_log = manifest.get("solver_log")
        if solver_log is not None:
            if not isinstance(solver_log, dict):
                raise OOFEMRunManifestError(
                    "Run manifest has invalid solver log"
                )
            self._safe_manifest_path(
                directory, solver_log.get("path"), "solver log"
            )
            if (
                isinstance(solver_log.get("size"), bool)
                or not isinstance(solver_log.get("size"), int)
                or solver_log.get("size") < 0
                or not isinstance(solver_log.get("mtime"), str)
                or not _is_sha256(solver_log.get("sha256"))
            ):
                raise OOFEMRunManifestError(
                    "Run manifest has invalid solver log metadata"
                )
        return manifest

    def _verify_input_file(self, directory, manifest):
        input_data = manifest["input"]
        if input_data["registered_at"] is not None:
            input_path = self._input_path(directory, manifest)
            if input_path.is_symlink() or not input_path.is_file():
                raise OOFEMRunManifestError("Registered input is missing or unsafe")
            if (
                input_path.stat().st_size != input_data["size"]
                or _sha256_file(input_path) != input_data["sha256"]
            ):
                raise OOFEMRunManifestError(
                    "Registered input checksum does not match"
                )

    def _verify_manifest_files(self, directory, manifest):
        self._verify_input_file(directory, manifest)
        for entry in manifest["result_files"]:
            path = self._safe_manifest_path(
                directory, entry["path"], "result"
            )
            if path.is_symlink() or not path.is_file():
                raise OOFEMRunManifestError("Result file is missing or unsafe")
            if path.stat().st_size != entry["size"]:
                raise OOFEMRunManifestError("Result file size does not match")
            if (
                entry.get("sha256") is not None
                and _sha256_file(path) != entry["sha256"]
            ):
                raise OOFEMRunManifestError(
                    "Result file checksum does not match"
                )
        solver_log = manifest.get("solver_log")
        if solver_log is not None:
            path = self._safe_manifest_path(
                directory, solver_log["path"], "solver log"
            )
            if path.is_symlink() or not path.is_file():
                raise OOFEMRunManifestError("Solver log is missing or unsafe")
            if (
                path.stat().st_size != solver_log["size"]
                or _sha256_file(path) != solver_log["sha256"]
            ):
                raise OOFEMRunManifestError(
                    "Solver log checksum does not match"
                )

    def load_run(self, run_id, verify_files=False):
        """Safely decode one manifest without accepting arbitrary paths."""
        directory = self._run_directory(run_id)
        manifest_path = directory / MANIFEST_FILE_NAME
        try:
            metadata = manifest_path.lstat()
        except OSError as error:
            raise OOFEMRunManifestError("Run manifest is missing") from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or manifest_path.is_symlink()
            or metadata.st_size > MAX_MANIFEST_BYTES
        ):
            raise OOFEMRunManifestError("Run manifest is not a safe regular file")
        try:
            with manifest_path.open("r", encoding="utf-8") as stream:
                manifest = json.load(stream)
        except (OSError, UnicodeError, ValueError) as error:
            raise OOFEMRunManifestError(
                "Could not decode run manifest"
            ) from error
        manifest = self._validate_manifest(directory, manifest)
        if verify_files:
            self._verify_manifest_files(directory, manifest)
        return manifest

    def handle_for(self, run_id):
        """Recreate a QProcess handle from a safely loaded manifest."""
        directory = self._run_directory(run_id)
        manifest = self.load_run(run_id)
        if manifest["input"]["registered_at"] is not None:
            self._verify_input_file(directory, manifest)
        return self._new_handle(directory, manifest)

    def get_handle(self, run_id):
        """Return absolute runtime paths for a safely loaded run."""
        return self.handle_for(run_id)

    def run_directory(self, run_id):
        """Return a validated run directory without rehashing its input."""
        directory = self._run_directory(run_id)
        self.load_run(run_id)
        return str(directory)

    def list_runs(self, status=None, limit=None):
        """Return newest-first valid manifests, skipping corrupt entries."""
        if status is not None and status not in RUN_STATUSES:
            raise ValueError("Unknown run status: {!r}".format(status))
        if limit is not None and (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 0
        ):
            raise ValueError("limit must be a non-negative integer or None")

        manifests = []
        for directory in self.root.iterdir():
            if (
                not directory.is_dir()
                or directory.is_symlink()
                or not _RUN_ID_RE.fullmatch(directory.name)
            ):
                continue
            try:
                manifest = self.load_run(directory.name)
            except OOFEMRunError:
                _logger.warning(
                    "Skipping invalid OOFEM run %s",
                    directory.name,
                )
                continue
            if status is None or manifest["status"] == status:
                manifests.append(manifest)
        manifests.sort(
            key=lambda item: (item["created_at"], item["run_id"]),
            reverse=True,
        )
        return manifests if limit is None else manifests[:limit]

    def history(self, status=None, limit=None):
        return self.list_runs(status=status, limit=limit)

    @_locked_run
    def delete_run(self, run_id):
        """Explicitly remove one non-running run after strict id validation."""
        manifest = self.load_run(run_id)
        if manifest["status"] == RUNNING:
            raise OOFEMRunStateError(
                "A running solver must be stopped before deleting its run"
            )
        directory = self._run_directory(run_id)
        _rmtree(directory)
        return True
