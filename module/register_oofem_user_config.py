#!/usr/bin/env python3
"""Register the OOFEM light module in SALOME's user GUI resources."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import stat
import tempfile
from xml.dom import Node, minidom

from oofem_preferences import PREFERENCE_DEFAULTS


OOFEM_SETTINGS = {
    "name": "OOFEM",
    "icon": "oofem.png",
    "library": "SalomePyQtGUILight",
    "version": "1.0",
}


def _salome_version(salome_dir: pathlib.Path) -> str:
    roots = [salome_dir / "INSTALL", salome_dir / "W64"]
    try:
        roots.extend(
            sorted(
                (
                    path
                    for path in salome_dir.glob("BINARIES-*")
                    if path.is_dir()
                ),
                key=lambda path: path.name,
            )
        )
    except OSError:
        # Keep the installed-layout lookup usable if the distribution root
        # cannot be enumerated (for example because of filesystem permissions).
        pass

    candidates = (
        root / component / "bin" / "salome" / "VERSION"
        for root in roots
        for component in ("GUI", "KERNEL")
    )
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"\b(\d+\.\d+(?:\.\d+)?(?:[a-z]+\d+)?)\b", text)
        if match:
            return match.group(1)
    raise RuntimeError("Cannot determine the SALOME GUI version")


def _user_config_names(version: str) -> tuple[str, ...]:
    """SALOME's own names for the per-user resource file, correct one first.

    SUIT_ResourceMgr picks the name by platform: ``SalomeApp.xml.<version>`` on
    Windows, ``SalomeApprc.<version>`` elsewhere. Preferring the wrong one is
    not cosmetic. A machine that has both -- a file carried over from another
    system, or left by an earlier run of this script -- gets OOFEM written into
    the one its GUI never reads, and the module is then missing from the module
    selector with no error reported anywhere at all.
    """
    windows_name = f"SalomeApp.xml.{version}"
    posix_name = f"SalomeApprc.{version}"
    if os.name == "nt":
        return (windows_name, posix_name)
    return (posix_name, windows_name)


def _default_config_path(salome_dir: pathlib.Path) -> pathlib.Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        root = pathlib.Path(config_home).expanduser()
    else:
        root = pathlib.Path.home() / ".config"
    salome_config_dir = root / "salome"
    version = _salome_version(salome_dir)
    for candidate_name in _user_config_names(version):
        candidate = salome_config_dir / candidate_name
        if candidate.exists():
            return candidate
    return salome_config_dir / _user_config_names(version)[0]


def _new_document():
    implementation = minidom.getDOMImplementation()
    doctype = implementation.createDocumentType("document", None, None)
    return implementation.createDocument(None, "document", doctype)


def _load_document(config_path: pathlib.Path):
    if not config_path.exists():
        return _new_document()
    document = minidom.parse(str(config_path))
    if document.documentElement.tagName != "document":
        raise RuntimeError(f"Invalid SALOME user resource root: {config_path}")
    return document


def _section(document, name: str):
    root = document.documentElement
    for child in root.childNodes:
        if (
            child.nodeType == Node.ELEMENT_NODE
            and child.tagName == "section"
            and child.getAttribute("name") == name
        ):
            return child
    section = document.createElement("section")
    section.setAttribute("name", name)
    root.insertBefore(section, root.firstChild)
    return section


def _set_parameter(document, section, name: str, value: str) -> bool:
    for child in section.childNodes:
        if (
            child.nodeType == Node.ELEMENT_NODE
            and child.tagName == "parameter"
            and child.getAttribute("name") == name
        ):
            if child.getAttribute("value") == value:
                return False
            child.setAttribute("value", value)
            return True
    parameter = document.createElement("parameter")
    parameter.setAttribute("name", name)
    parameter.setAttribute("value", value)
    section.appendChild(parameter)
    return True


def _set_default_parameter(document, section, name: str, value: str) -> bool:
    """Add a default without overwriting a value selected by the user."""
    for child in section.childNodes:
        if (
            child.nodeType == Node.ELEMENT_NODE
            and child.tagName == "parameter"
            and child.getAttribute("name") == name
        ):
            return False
    return _set_parameter(document, section, name, value)


def register(salome_dir: pathlib.Path, config_path: pathlib.Path | None = None):
    salome_dir = salome_dir.resolve()
    config_path = config_path or _default_config_path(salome_dir)
    config_path = config_path.expanduser().resolve()
    document = _load_document(config_path)

    changed = False
    module_section = _section(document, "OOFEM")
    for name, value in OOFEM_SETTINGS.items():
        changed = _set_parameter(document, module_section, name, value) or changed
    for name, value in PREFERENCE_DEFAULTS.items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        changed = _set_default_parameter(document, module_section, name, text) or changed

    resource_path = (
        salome_dir
        / "INSTALL"
        / "OOFEM"
        / "share"
        / "salome"
        / "resources"
        / "oofem"
    )
    resources_section = _section(document, "resources")
    changed = (
        _set_parameter(document, resources_section, "OOFEM", str(resource_path))
        or changed
    )

    if not changed:
        return config_path, False

    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = config_path.with_name(config_path.name + ".before-oofem")
    if config_path.exists() and not backup_path.exists():
        shutil.copy2(config_path, backup_path)

    previous_mode = (
        stat.S_IMODE(config_path.stat().st_mode) if config_path.exists() else 0o600
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=config_path.name + ".", dir=str(config_path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(document.toprettyxml(indent=" ", encoding="utf-8"))
        os.chmod(temporary_name, previous_mode)
        os.replace(temporary_name, config_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return config_path, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--salome", required=True, type=pathlib.Path)
    parser.add_argument("--config", type=pathlib.Path)
    arguments = parser.parse_args()
    path, changed = register(arguments.salome, arguments.config)
    action = "Registered OOFEM in" if changed else "OOFEM already registered in"
    print(f"{action} SALOME user GUI: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
