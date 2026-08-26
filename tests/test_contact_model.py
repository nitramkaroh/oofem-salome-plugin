import pathlib
import sys
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from OOFEMSalomePlugin.OOFEMContact import (  # noqa: E402
    canonical_contact,
    contact_element_spec,
    orient_contact_nodes,
)


class ContactModelTests(unittest.TestCase):
    def test_canonicalizes_current_contact_pair(self):
        value = canonical_contact(
            {
                "name": "pair",
                "master": "MASTER",
                "slave": "SLAVE",
                "params": {"pn": "1000", "pt": 500, "friction": 0},
            },
            "planestrain",
        )
        self.assertEqual(value["master_group"], "MASTER")
        self.assertEqual(value["params"]["normal_penalty"], 1000.0)
        self.assertEqual(value["params"]["algorithm"], 0)

    def test_rejects_invalid_values_and_2d_sweep_and_prune(self):
        base = {"master_group": "M", "slave_group": "S", "params": {}}
        for invalid in (float("nan"), float("inf"), 0.0, -1.0):
            value = dict(base)
            value["params"] = {"normal_penalty": invalid}
            with self.assertRaises(ValueError):
                canonical_contact(value, "3d")
        value = dict(base)
        value["params"] = {"algorithm": 1}
        with self.assertRaisesRegex(ValueError, "only in 3D"):
            canonical_contact(value, "2dplanestress")

        value = dict(base)
        value["params"] = {"algorithm": 0.5}
        with self.assertRaisesRegex(ValueError, "must be 0 or 1"):
            canonical_contact(value, "planestrain")

    def test_contact_element_types_and_orientation(self):
        self.assertEqual(
            contact_element_spec("planestrain", 2),
            ("StructuralContactElement_LineLin", 2),
        )
        self.assertEqual(
            contact_element_spec("3d", 3),
            ("StructuralContactElement_TrLin", 3),
        )
        self.assertEqual(
            contact_element_spec("3d", 4),
            ("StructuralContactElement_QuadLin", 4),
        )
        self.assertEqual(orient_contact_nodes([1, 2], True), [2, 1])
        self.assertEqual(orient_contact_nodes([1, 2, 3, 4], True), [1, 4, 3, 2])

    def test_boolean_options_are_parsed_without_string_truthiness(self):
        contact = {
            "master_group": " M ",
            "slave_group": " S ",
            "params": {"two_pass": "false", "reverse_master": "yes"},
        }
        value = canonical_contact(contact, "planestrain")
        self.assertEqual(value["master_group"], "M")
        self.assertFalse(value["params"]["two_pass"])
        self.assertTrue(value["params"]["reverse_master"])

        contact["params"]["reverse_slave"] = "sometimes"
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            canonical_contact(contact, "planestrain")

    def test_rejects_non_object_parameters(self):
        with self.assertRaisesRegex(ValueError, "parameters must be an object"):
            canonical_contact(
                {"master_group": "M", "slave_group": "S", "params": []},
                "planestrain",
            )


if __name__ == "__main__":
    unittest.main()
