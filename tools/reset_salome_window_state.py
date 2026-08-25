#!/usr/bin/env python3
"""Drop SALOME's remembered per-module window layout for OOFEM.

SALOME saves which docks/toolbars were visible separately for every
module, keyed by module name, and restores that layout *after* a module's
activate() callback returns. A layout that records the OOFEM dock as
hidden therefore makes activating OOFEM look like it did nothing, while
the very same dock stays visible under the "nomodule" layout -- the
confusing "panel only shows when the selector says SALOME" symptom.

Layouts like that are easy to end up with: the headless acceptance
scripts in this directory drive a real desktop and save whatever state
they left behind. ``OOFEMModule`` now re-asserts dock visibility after
the restore, so this script is a cleanup, not a prerequisite -- but it
also clears an unwanted remembered "OOFEM Log" debug console, which the
runtime fix deliberately does not fight.

Only the window-layout entries are touched. The module registration
(``<section name="OOFEM">`` with name/icon/library) and every unrelated
preference are preserved byte for byte.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile


LAYOUT_SECTIONS = (
    "windows_visibility",
    "windows_geometry",
    "windows_geometry_version",
)
# "" is SALOME's key for the pre-module default layout.
LAYOUT_KEYS = ("OOFEM", "nomodule", "")

_SECTION_RE = re.compile(r'<section\s+name="([^"]*)"')
_END_SECTION_RE = re.compile(r"</section>")
_NAME_RE = re.compile(r'\bname="([^"]*)"')


def default_config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    directory = os.path.join(base, "salome")
    candidates = sorted(
        name
        for name in os.listdir(directory)
        if name.startswith("SalomeApprc.")
    ) if os.path.isdir(directory) else []
    if not candidates:
        raise SystemExit(
            "No SalomeApprc.* found under {}; pass --config explicitly.".format(
                directory
            )
        )
    return os.path.join(directory, candidates[-1])


def salome_is_running():
    try:
        result = subprocess.run(
            ["pgrep", "-f", "SALOME_Session_Server"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.strip())


def strip_layout_entries(lines):
    """Return (kept_lines, removed) with layout entries for OOFEM dropped."""
    kept = []
    removed = []
    section = None
    for line in lines:
        match = _SECTION_RE.search(line)
        if match:
            section = match.group(1)
            kept.append(line)
            continue
        if _END_SECTION_RE.search(line):
            section = None
            kept.append(line)
            continue
        if section in LAYOUT_SECTIONS and "<parameter" in line:
            name_match = _NAME_RE.search(line)
            if name_match and name_match.group(1) in LAYOUT_KEYS:
                removed.append((section, name_match.group(1)))
                continue
        kept.append(line)
    return kept, removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        help="path to SalomeApprc.<version> (default: newest under ~/.config/salome)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be removed without writing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="proceed even while SALOME is running (it will overwrite on exit)",
    )
    args = parser.parse_args(argv)

    path = args.config or default_config_path()
    if not os.path.isfile(path):
        raise SystemExit("Not a file: {}".format(path))

    if salome_is_running() and not (args.dry_run or args.force):
        raise SystemExit(
            "SALOME is running and rewrites this file when it exits, which "
            "would undo the cleanup.\nClose SALOME first, or pass --force."
        )

    with open(path, "r", encoding="utf-8") as stream:
        lines = stream.readlines()

    kept, removed = strip_layout_entries(lines)
    if not removed:
        print("No remembered OOFEM window layout in {}".format(path))
        return 0

    for section, key in removed:
        print("remove {}/{!r}".format(section, key or "<default>"))

    if args.dry_run:
        print("(dry run; nothing written)")
        return 0

    backup = path + ".before-window-reset"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
        print("backup: {}".format(backup))

    directory = os.path.dirname(path) or "."
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.writelines(kept)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    print(
        "Cleared {} layout entrie(s) in {}. Start SALOME again; OOFEM's panel "
        "will use a fresh layout.".format(len(removed), path)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
