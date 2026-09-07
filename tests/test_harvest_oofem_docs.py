"""Tests for the OOFEM documentation harvester.

The regression that matters here is scope.  OOFEM parameter keys are namespaced
per record -- ``E`` is a Young's modulus in one material and a lattice modulus
in another, ``k`` is a bulk modulus or a compressive-to-tensile ratio -- so a
harvester that matches on the bare key attaches confidently worded, completely
wrong prose to a parameter.  Wrong documentation is worse than none, so the
scoping is pinned down by tests.
"""

import importlib.util
import os
import textwrap
import unittest

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPOSITORY_ROOT, "tools", "harvest_oofem_docs.py")

def _load(name, path):
    """Load one module by path, without touching sys.path or sys.modules."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest_module = _load("harvest_oofem_docs", SCRIPT)
coercion_module = _load(
    "oofem_parameter_coercion",
    os.path.join(
        REPOSITORY_ROOT, "src", "OOFEMSalomePlugin", "OOFEMParameterCoercion.py"
    ),
)
parameter_tooltip = coercion_module.parameter_tooltip


def _write(directory, name, text):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w") as handle:
        handle.write(textwrap.dedent(text))


class TidyTests(unittest.TestCase):
    def test_math_role_keeps_its_symbol(self):
        # losing it leaves "CALM  control parameter. For  = 0 ..."
        tidied = harvest_module._tidy(r"CALM :math:`\psi` control parameter")
        self.assertIn("psi", tidied.lower())
        self.assertNotIn(":math:", tidied)
        self.assertNotIn("`", tidied)

    def test_strips_rst_and_latex_markup(self):
        tidied = harvest_module._tidy(
            r"see ``nsteps`` and :ref:`sparselinsolver` with \param{d} left"
        )
        for junk in ("``", ":ref:", "\\param", "{", "}"):
            self.assertNotIn(junk, tidied)

    def test_completes_a_sentence_fragment(self):
        # the manuals routinely start mid-sentence: "is time step length"
        self.assertEqual(
            harvest_module._tidy("is time step length"), "Time step length."
        )


class HarvestScopeTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.directory = tempfile.mkdtemp(prefix="oofem-doc-")
        self.addCleanup(__import__("shutil").rmtree, self.directory, True)

    def test_same_key_in_two_records_stays_apart(self):
        _write(
            os.path.join(self.directory, "matlibmanual"),
            "matlibmanual.tex",
            r"""
            \subsubsection{Truss element - Truss}
            Parameters &- \param{A} cross-section area of the bar\\
            \subsubsection{Moisture transport - Moisture}
            Parameters &- \param{A} sorption isotherm coefficient\\
            """,
        )
        found = harvest_module.harvest(self.directory)
        self.assertIn("area", found[("truss", "a")].lower())
        self.assertIn("sorption", found[("moisture", "a")].lower())

    def test_equation_labels_do_not_capture_a_section(self):
        # \label inside a section is often an equation's; the record name is the
        # part of the heading after the dash
        _write(
            os.path.join(self.directory, "matlibmanual"),
            "matlibmanual.tex",
            r"""
            \subsubsection{Mises plasticity model - MisesMat}
            \begin{equation}\label{VMvonMisesCondition}
            \end{equation}
            Parameters &- \param{h} hardening modulus, may be negative\\
            """,
        )
        found = harvest_module.harvest(self.directory)
        self.assertIn(("misesmat", "h"), found)
        self.assertNotIn(("vmvonmisescondition", "h"), found)

    def test_rst_records_are_scoped_by_anchor(self):
        _write(
            os.path.join(self.directory, "oofemInput"),
            "analysisrecords.rst",
            """
            .. _NonLinearStatic:

            NonLinearStatic
            ~~~~~~~~~~~~~~~

            -  ``maxiter`` - the iteration limit; restart follows when reached.

            .. _LinearStatic:

            LinearStatic
            ~~~~~~~~~~~~

            -  ``maxiter`` - unused by the direct solver.
            """,
        )
        found = harvest_module.harvest(self.directory)
        self.assertIn("restart", found[("nonlinearstatic", "maxiter")].lower())
        self.assertIn("direct solver", found[("linearstatic", "maxiter")].lower())

    def test_unknown_record_is_dropped_rather_than_guessed(self):
        _write(
            os.path.join(self.directory, "matlibmanual"),
            "matlibmanual.tex",
            r"""
            \subsubsection{A model with no keyword in its heading}
            Parameters &- \param{E} some modulus\\
            """,
        )
        self.assertEqual(harvest_module.harvest(self.directory), {})


class TooltipTests(unittest.TestCase):
    def test_manual_text_follows_the_plugin_wording(self):
        tooltip = parameter_tooltip(
            {"description": "Own text.", "doc_description": "Manual text."}
        )
        self.assertLess(tooltip.index("Own text."), tooltip.index("Manual text."))
        self.assertIn("OOFEM manual", tooltip)

    def test_plain_text_when_there_is_no_manual_entry(self):
        self.assertEqual(parameter_tooltip({"description": "Own text."}), "Own text.")

    def test_markup_in_a_description_is_escaped(self):
        tooltip = parameter_tooltip(
            {"description": "a < b & c", "doc_description": "x > y"}
        )
        self.assertIn("&lt;", tooltip)
        self.assertIn("&amp;", tooltip)
        self.assertIn("&gt;", tooltip)


if __name__ == "__main__":
    unittest.main()
