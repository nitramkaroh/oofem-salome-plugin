#!/usr/bin/env python3
"""Fill the parameter templates' ``doc_description`` from OOFEM's own manuals.

    tools/harvest_oofem_docs.py --oofem-doc /path/to/oofem/doc
    tools/harvest_oofem_docs.py --oofem-doc ... --check
    tools/harvest_oofem_docs.py --oofem-doc ... --report

The hand-written ``description`` of each parameter stays untouched and remains
what the UI shows first: it is written for someone meeting the parameter in this
plugin, and for a fair number of parameters it is *better* than the manual --
``matlibmanual`` documents ``E`` as bare "Young modulus" where the template says
which unit system it is read in.  The manual wins on the parameters that carry
real behaviour, though (what happens when ``maxiter`` is hit, what each
``stiffmode`` value selects), so it is stored alongside rather than instead, and
only when it actually adds something.

This is an occasional generation step, not a runtime dependency.  The harvested
text is committed with the templates, so an installed plugin never needs the
OOFEM documentation tree.
"""

import argparse
import json
import os
import re
import sys

TEMPLATE_FILES = (
    "OOFEMAnalyses.json",
    "OOFEMBCs.json",
    "OOFEMCrossSections.json",
    "OOFEMMaterials.json",
    "OOFEMTimeFunctions.json",
)
PACKAGE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src",
    "OOFEMSalomePlugin",
)
# Only replace the hand-written text when the manual says materially more.
MIN_GAIN = 1.2


def _tidy(text):
    """Collapse manual markup into one plain-text paragraph."""
    text = re.sub(r":ref:`([^`<]*?)(?:\s*<[^>]*>)?`", r"\1", text)
    # :math:`\psi` must give up its symbol before backslash macros are stripped
    # wholesale, or the sentence loses the very quantity it is about and reads
    # "CALM  control parameter. For  = 0 displacement control is applied."
    text = re.sub(
        r":math:`([^`]*)`",
        lambda m: re.sub(r"[\\{}]", "", m.group(1)).strip() or "",
        text,
    )
    text = re.sub(r"``([^`]*)``", r"\1", text)
    text = re.sub(r"\\(?:param|elemparam|descitem)\{([^}]*)\}(?:\{[^}]*\})?", r"\1", text)
    text = re.sub(r"\\(?:ref|label|cite)\{[^}]*\}", "", text)
    text = re.sub(r"\\[A-Za-z]+", "", text)
    text = text.replace("~", " ").replace("\\", " ")
    text = re.sub(r"[{}$]", "", text)
    text = " ".join(text.split())
    # the manuals routinely start mid-sentence: "is time step length"
    text = re.sub(r"^(?:is|are|determines|causes|denotes)\b\s*", "", text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    if text and text[-1] not in ".!?":
        text += "."
    return text


def harvest(doc_root):
    """Return {(record, key): description} pulled from the manuals.

    Scoped by record on purpose.  OOFEM keys are namespaced per record -- ``E``
    is a Young's modulus in one material and a lattice modulus in another, ``A``
    is a truss area here and a sorption-isotherm coefficient there, ``k`` is a
    bulk modulus or a compressive-to-tensile ratio -- so matching on the bare key
    attaches confidently worded, completely wrong prose to a parameter.  A
    missing description is recoverable; a wrong one is not, so nothing is
    recorded that cannot be tied to the record it came from.
    """
    found = {}

    def offer(record, key, description):
        if not record:
            return
        index = (record.lower(), key.lower())
        description = _tidy(description)
        if len(description) > len(found.get(index, "")):
            found[index] = description

    # reStructuredText: sections are introduced by ".. _Record:" anchors, and
    # the parameters of each appear as "-  ``key`` - prose" inside it.
    for sub in ("oofemInput", "usermanual"):
        directory = os.path.join(doc_root, sub)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".rst"):
                continue
            with open(os.path.join(directory, name), errors="replace") as handle:
                text = handle.read()
            pieces = re.split(r"^\.\.\s+_([A-Za-z_]\w*):\s*$", text, flags=re.M)
            # pieces = [preamble, name1, body1, name2, body2, ...]
            for position in range(1, len(pieces) - 1, 2):
                record, body = pieces[position], pieces[position + 1]
                for match in re.finditer(
                    r"^-\s+``([A-Za-z_]\w*)``\s*[-\u2013]\s*(.+?)"
                    # \Z: without it the last bullet of a section is dropped
                    r"(?=\n\s*\n|\n-\s+``|\Z)",
                    body,
                    re.S | re.M,
                ):
                    offer(record, match.group(1), match.group(2))

    # LaTeX: each model is a \subsubsection carrying a \label{Record}; its
    # parameters are "&- \param{key} prose\\" rows of the following table.
    for sub in ("matlibmanual", "elementlibmanual"):
        directory = os.path.join(doc_root, sub)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".tex"):
                continue
            with open(os.path.join(directory, name), errors="replace") as handle:
                text = handle.read()
            pieces = re.split(r"\\subsubsection\{", text)
            for piece in pieces[1:]:
                # The name after the dash in the heading is the input keyword:
                # "Mises plasticity model with isotropic damage - MisesMat" is
                # the material OOFEM reads as "misesmat".  \label is NOT a
                # substitute -- the first label inside a section is often an
                # equation's (VMvonMisesCondition), which silently mis-files
                # every parameter of that model.
                heading = re.match(r"([^}]*)\}", piece)
                record = None
                if heading and " - " in heading.group(1):
                    record = heading.group(1).rsplit(" - ", 1)[1].strip()
                if not record:
                    label = re.search(r"\\label\{([A-Za-z_][\w]*)\}", piece)
                    if label:
                        record = label.group(1)
                if not record:
                    continue
                for match in re.finditer(
                    r"\\(?:param|elemparam)\{([A-Za-z_]\w*)\}(?:\{[^}]*\})?"
                    r"\s*([^\\\n]{3,300}?)\s*\\\\",
                    piece,
                ):
                    offer(record, match.group(1), match.group(2))

    return found


def _walk_parameters(node, record=None):
    """Yield (record, parameter dict) for every parameter in a template file."""
    if isinstance(node, dict):
        record = node.get("oofem_name", record)
        if "key" in node and "type" in node:
            yield record, node
        for value in node.values():
            for item in _walk_parameters(value, record):
                yield item
    elif isinstance(node, list):
        for value in node:
            for item in _walk_parameters(value, record):
                yield item


def _object_span(text, position):
    """Return the (start, end) of the JSON object enclosing ``position``."""
    depth = 0
    begin = None
    for index in range(position, -1, -1):
        if text[index] == "}":
            depth += 1
        elif text[index] == "{":
            if depth == 0:
                begin = index
                break
            depth -= 1
    if begin is None:
        return None
    depth = 0
    for index in range(begin, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return begin, index + 1
    return None


_DESCRIPTION_RE = re.compile(r'"description"\s*:\s*"(?:[^"\\]|\\.)*"')
_DOC_RE = re.compile(r',?\s*"doc_description"\s*:\s*"(?:[^"\\]|\\.)*"')


def _edit_parameter(text, record, key, wanted):
    """Insert, replace or drop one parameter's doc_description in raw JSON text.

    Done as text surgery rather than json.dumps round-tripping on purpose.
    These template files mix compact one-line parameter objects with expanded
    ones, and re-serialising them reformats every file it touches: 533 changed
    lines for 13 real additions, in files the harvest had no business rewriting
    at all.  A reviewable diff is worth the fiddlier code.
    """
    for parameter_match in re.finditer(
        r'"key"\s*:\s*"' + re.escape(key) + r'"', text
    ):
        span = _object_span(text, parameter_match.start())
        if span is None:
            continue
        begin, finish = span
        # the record this object belongs to is the nearest oofem_name above it
        owner = None
        for owner_match in re.finditer(r'"oofem_name"\s*:\s*"([^"]*)"', text[:begin]):
            owner = owner_match.group(1)
        if owner is None or owner.lower() != record:
            continue

        body = text[begin:finish]
        stripped = _DOC_RE.sub("", body)
        if wanted is None:
            return text[:begin] + stripped + text[finish:] if stripped != body else text

        description = _DESCRIPTION_RE.search(stripped)
        if description is None:
            return text
        encoded = json.dumps(wanted, ensure_ascii=False)
        insert_at = description.end()
        # match the surrounding layout: expanded objects get their own line
        newline = stripped.rfind("\n", 0, description.start())
        if newline == -1:
            addition = ', "doc_description": {}'.format(encoded)
        else:
            indent = ""
            for character in stripped[newline + 1:]:
                if character == " ":
                    indent += " "
                else:
                    break
            addition = ',\n{}"doc_description": {}'.format(indent, encoded)
        updated = stripped[:insert_at] + addition + stripped[insert_at:]
        return text[:begin] + updated + text[finish:]
    return text


def apply(found, check=False, report=False):
    added = kept = cleared = 0
    stale = []
    lines = []

    for name in TEMPLATE_FILES:
        path = os.path.join(PACKAGE, name)
        if not os.path.isfile(path):
            continue
        with open(path) as handle:
            original = handle.read()
        text = original
        data = json.loads(original)

        for record, parameter in _walk_parameters(data):
            own = parameter.get("description", "")
            manual = found.get((str(record).lower(), parameter["key"].lower()))
            wanted = None
            if manual and len(manual) > len(own) * MIN_GAIN:
                wanted = manual
            current = parameter.get("doc_description")
            if wanted == current:
                if wanted:
                    kept += 1
                continue
            if check:
                stale.append((name, parameter["key"], current, wanted))
                continue
            text = _edit_parameter(text, str(record).lower(), parameter["key"], wanted)
            if wanted is None:
                cleared += 1
            else:
                added += 1
                lines.append((parameter["key"], own, wanted))

        if not check and text != original:
            # never write something that no longer parses
            json.loads(text)
            with open(path, "w") as handle:
                handle.write(text)
            print("updated {}".format(name))

    if check:
        if stale:
            for name, key, current, wanted in stale:
                sys.stderr.write(
                    "STALE: {} {}: stored {!r}, manual gives {!r}\n".format(
                        name, key, (current or "")[:40], (wanted or "")[:40]
                    )
                )
            sys.stderr.write(
                "\n{} parameter(s) out of date; run "
                "tools/harvest_oofem_docs.py\n".format(len(stale))
            )
            return 1
        print("doc_description is up to date ({} carried)".format(kept))
        return 0

    print(
        "harvested {} manual entries; {} written, {} unchanged, {} removed".format(
            len(found), added, kept, cleared
        )
    )
    if report:
        for key, own, manual in lines[:40]:
            print("\n  [{}]\n    own:    {}\n    manual: {}".format(key, own, manual))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--oofem-doc",
        required=True,
        help="path to the OOFEM source tree's doc/ directory",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any stored doc_description differs from the manual",
    )
    parser.add_argument(
        "--report", action="store_true", help="print every harvested description"
    )
    arguments = parser.parse_args(argv)

    doc_root = os.path.abspath(os.path.expanduser(arguments.oofem_doc))
    if not os.path.isdir(doc_root):
        parser.error("not a directory: {}".format(doc_root))

    found = harvest(doc_root)
    if not found:
        sys.stderr.write("ERROR: no parameter documentation found under {}\n".format(doc_root))
        return 1
    return apply(found, check=arguments.check, report=arguments.report)


if __name__ == "__main__":
    raise SystemExit(main())
