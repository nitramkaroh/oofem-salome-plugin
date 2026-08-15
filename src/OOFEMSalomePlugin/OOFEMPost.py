"""OOFEM result discovery, ParaVis loading, and optional MED conversion."""

import glob
import os


SUPPORTED_RESULT_EXTENSIONS = (".pvd", ".vtu", ".vtk", ".med", ".out")


def output_path_from_input(input_file):
    input_file = os.path.abspath(input_file)
    try:
        with open(input_file, "r", encoding="utf-8") as stream:
            first_line = stream.readline().strip()
    except OSError:
        first_line = ""
    if first_line:
        if not os.path.isabs(first_line):
            first_line = os.path.join(os.path.dirname(input_file), first_line)
        return os.path.abspath(first_line)
    return os.path.splitext(input_file)[0] + ".out"


def discover_result_files(input_file):
    output_file = output_path_from_input(input_file)
    candidates = [output_file]
    candidates.extend(glob.glob(output_file + ".m*.pvd"))
    candidates.extend(glob.glob(output_file + ".m*.vtu"))
    input_stem = os.path.splitext(os.path.abspath(input_file))[0]
    candidates.extend(glob.glob(input_stem + "*.vtk"))
    candidates.extend(glob.glob(input_stem + "*.med"))

    priority = {".pvd": 0, ".med": 1, ".vtu": 2, ".vtk": 3, ".out": 4}
    unique = {
        os.path.abspath(path)
        for path in candidates
        if os.path.isfile(path)
        and os.path.splitext(path)[1].lower() in SUPPORTED_RESULT_EXTENSIONS
    }
    return sorted(
        unique,
        key=lambda path: (priority.get(os.path.splitext(path)[1].lower(), 99), path),
    )


def preferred_visualization_file(paths):
    for extension in (".pvd", ".med", ".vtu", ".vtk"):
        for path in paths:
            if path.lower().endswith(extension):
                return path
    return None


def open_in_paravis(path, salome_context=None):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    if os.path.splitext(path)[1].lower() not in (".pvd", ".vtu", ".vtk", ".med"):
        raise ValueError("ParaVis cannot open OOFEM text output; select a VTK/PVD/MED file.")

    try:
        if salome_context is not None:
            salome_context.sg.activateModule("ParaViS")
        else:
            import salome

            salome.sg.activateModule("ParaViS")
    except (AttributeError, ImportError, RuntimeError):
        pass

    try:
        import pvsimple as pvs
    except ImportError as error:
        raise RuntimeError(
            "ParaVis Python API is unavailable. Activate/install the SALOME ParaVis module."
        ) from error

    source = pvs.OpenDataFile(path)
    if source is None:
        raise RuntimeError("ParaVis did not create a reader for {}".format(path))
    pvs.Show(source)
    try:
        pvs.ResetCamera()
    except AttributeError:
        pass
    pvs.Render()
    return source


def convert_vtk_to_med(source_path, destination_path=None):
    source_path = os.path.abspath(source_path)
    if os.path.splitext(source_path)[1].lower() not in (".vtu", ".vtk"):
        raise ValueError("MED conversion accepts a single .vtu or .vtk result file.")
    if destination_path is None:
        destination_path = os.path.splitext(source_path)[0] + ".med"
    destination_path = os.path.abspath(destination_path)
    try:
        import meshio
    except ImportError as error:
        raise RuntimeError(
            "VTK-to-MED conversion requires the optional 'meshio' Python package."
        ) from error
    mesh = meshio.read(source_path)
    meshio.write(destination_path, mesh)
    return destination_path
