"""Safe, serializable descriptions of OOFEM run results.

The module has no Qt or SALOME dependencies.  It deliberately treats a run
directory as a security boundary: symlinks and PVD references escaping that
directory are ignored.
"""

import json
import os
import xml.etree.ElementTree as ET


RESULT_EXTENSIONS = (".pvd", ".med", ".vtu", ".vtk", ".msh", ".out")
_AUXILIARY_NAMES = ("run.json", "manifest.json", "solver.log")
_PRIORITY = {
    ".pvd": 0,
    ".med": 1,
    ".vtu": 2,
    ".vtk": 3,
    ".msh": 4,
    ".out": 5,
    ".log": 6,
    ".in": 7,
    ".json": 8,
}


def _contained_path(root, path):
    root = os.path.realpath(os.path.abspath(root))
    resolved = os.path.realpath(os.path.abspath(path))
    try:
        return os.path.commonpath((root, resolved)) == root
    except ValueError:
        return False


def _safe_file(root, path):
    return (
        not os.path.islink(path)
        and _contained_path(root, path)
        and os.path.isfile(path)
    )


def discover_run_files(run_directory, include_auxiliary=False):
    """Return supported files below *run_directory* in display priority order."""
    root = os.path.realpath(os.path.abspath(run_directory))
    if not os.path.isdir(root):
        return []

    paths = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if not os.path.islink(os.path.join(current, name))
            and _contained_path(root, os.path.join(current, name))
        ]
        for name in filenames:
            path = os.path.join(current, name)
            extension = os.path.splitext(name)[1].lower()
            supported = extension in RESULT_EXTENSIONS
            auxiliary = include_auxiliary and (
                extension == ".in" or name.lower() in _AUXILIARY_NAMES
            )
            if (supported or auxiliary) and _safe_file(root, path):
                paths.append(os.path.abspath(path))

    return sorted(
        set(paths),
        key=lambda path: (
            _PRIORITY.get(os.path.splitext(path)[1].lower(), 99),
            os.path.relpath(path, root).lower(),
        ),
    )


def _manifest_path(root, relative_path):
    if not isinstance(relative_path, str) or not relative_path:
        return None
    relative_path = relative_path.replace("\\", "/")
    if relative_path.startswith("/") or ".." in relative_path.split("/"):
        return None
    candidate = os.path.join(root, *relative_path.split("/"))
    return os.path.abspath(candidate) if _safe_file(root, candidate) else None


def _manifest_result_files(root, manifest):
    entries = manifest.get("result_files")
    if not isinstance(entries, list):
        return None
    paths = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = _manifest_path(root, entry.get("path"))
        if path is None:
            continue
        size = entry.get("size")
        if isinstance(size, int) and not isinstance(size, bool):
            try:
                if os.path.getsize(path) != size:
                    continue
            except OSError:
                continue
        paths.append(path)
    return paths


def _manifest_auxiliary_files(root, manifest):
    paths = []
    manifest_file = os.path.join(root, "run.json")
    if _safe_file(root, manifest_file):
        paths.append(os.path.abspath(manifest_file))
    input_record = manifest.get("input")
    if isinstance(input_record, dict):
        path = _manifest_path(root, input_record.get("path"))
        if path:
            paths.append(path)
    log_record = manifest.get("solver_log")
    if isinstance(log_record, dict):
        path = _manifest_path(root, log_record.get("path"))
        if path:
            paths.append(path)
    return paths


def _sort_paths(root, paths):
    return sorted(
        set(paths),
        key=lambda path: (
            _PRIORITY.get(os.path.splitext(path)[1].lower(), 99),
            os.path.relpath(path, root).lower(),
        ),
    )


def preferred_visualization_file(paths):
    """Choose a time collection before individual visualization files."""
    for extension in (".pvd", ".med", ".vtu", ".vtk"):
        for path in paths:
            if str(path).lower().endswith(extension):
                return path
    return None


def read_pvd_time_steps(path, run_directory=None):
    """Return valid PVD datasets as ``[{time, file}]`` dictionaries."""
    if run_directory is None:
        run_directory = os.path.dirname(os.path.abspath(path))
    root = os.path.realpath(os.path.abspath(run_directory))
    if not _safe_file(root, path):
        return []
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return []

    time_steps = []
    for dataset in tree.iter():
        if dataset.tag.rsplit("}", 1)[-1] != "DataSet":
            continue
        filename = (dataset.get("file") or "").strip()
        if not filename:
            continue
        candidate = os.path.abspath(os.path.join(os.path.dirname(path), filename))
        if not _safe_file(root, candidate):
            continue
        raw_time = dataset.get("timestep")
        try:
            time_value = float(raw_time) if raw_time not in (None, "") else None
        except ValueError:
            time_value = raw_time
        time_steps.append(
            {
                "time": time_value,
                "file": candidate,
                "part": dataset.get("part"),
            }
        )
    return time_steps


def read_vtu_fields(path, run_directory=None):
    """Return PointData and CellData array names from an XML VTU file."""
    if run_directory is None:
        run_directory = os.path.dirname(os.path.abspath(path))
    root = os.path.realpath(os.path.abspath(run_directory))
    if not _safe_file(root, path):
        return {"point": [], "cell": []}
    fields = {"point": [], "cell": []}
    section = None
    try:
        # Handed a path, iterparse closes the file only once the iterator is
        # exhausted -- and this loop deliberately breaks out early, as soon as
        # the field names are known. The dangling handle keeps the .vtu locked
        # on Windows, where deleting a run then fails with WinError 32. Owning
        # the handle here closes it however the loop ends.
        with open(path, "rb") as handle:
            for event, element in ET.iterparse(handle, events=("start", "end")):
                tag = element.tag.rsplit("}", 1)[-1]
                if event == "start" and tag == "PointData":
                    section = "point"
                elif event == "start" and tag == "CellData":
                    section = "cell"
                elif event == "start" and tag == "DataArray" and section:
                    name = (element.get("Name") or "").strip()
                    if name and name not in fields[section]:
                        fields[section].append(name)
                elif event == "end" and tag in ("PointData", "CellData"):
                    section = None
                elif event == "start" and tag in ("Points", "Cells"):
                    # VTK XML declares result fields before large mesh arrays.
                    break
                elif event == "end" and tag == "Piece":
                    break
                if event == "end":
                    element.clear()
    except (ET.ParseError, OSError):
        return {"point": [], "cell": []}
    return fields


def _read_manifest(run_directory):
    for filename in ("run.json", "manifest.json"):
        path = os.path.join(run_directory, filename)
        if not _safe_file(run_directory, path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as stream:
                value = json.load(stream)
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def summarize_run(run_directory, manifest=None):
    """Build a JSON-serializable result summary for one run directory."""
    root = os.path.realpath(os.path.abspath(run_directory))
    if not os.path.isdir(root):
        return {
            "run_directory": root,
            "exists": False,
            "manifest": {},
            "files": [],
            "visualization_file": None,
            "time_steps": [],
            "fields": {"point": [], "cell": []},
        }

    if not isinstance(manifest, dict):
        manifest = _read_manifest(root)
    manifested_results = _manifest_result_files(root, manifest)
    terminal = manifest.get("status") in (
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
    )
    if terminal and manifested_results is not None:
        result_files = manifested_results
        files = _sort_paths(
            root, result_files + _manifest_auxiliary_files(root, manifest)
        )
    else:
        # Pending/running and legacy manifests may be inspected live.  Once a
        # run is terminal, only its registered result_files are trusted.
        result_files = discover_run_files(root, include_auxiliary=False)
        files = _sort_paths(
            root,
            result_files + discover_run_files(root, include_auxiliary=True),
        )
    visualization = preferred_visualization_file(result_files)
    pvd = next(
        (path for path in result_files if path.lower().endswith(".pvd")), None
    )
    time_steps = read_pvd_time_steps(pvd, root) if pvd else []
    registered = {os.path.realpath(path) for path in result_files}
    time_steps = [
        step for step in time_steps if os.path.realpath(step["file"]) in registered
    ]
    vtu = None
    if time_steps:
        vtu = time_steps[0]["file"]
    if vtu is None:
        vtu = next(
            (path for path in result_files if path.lower().endswith(".vtu")),
            None,
        )
    fields = read_vtu_fields(vtu, root) if vtu else {"point": [], "cell": []}

    return {
        "run_directory": root,
        "exists": True,
        "manifest": manifest,
        "files": files,
        "visualization_file": visualization,
        "time_steps": time_steps,
        "fields": fields,
    }
