import pathlib
import sys
import types
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from OOFEMSalomePlugin import plugin_entry  # noqa: E402


class PluginEntryTests(unittest.TestCase):
    def test_registration_is_lazy_and_uses_salome_signature(self):
        module_name = "OOFEMSalomePlugin.OOFEMModule"
        module_before = sys.modules.get(module_name)
        calls = []

        def add_function(*arguments):
            calls.append(arguments)

        plugin_entry.register_plugin(add_function)
        module_after = sys.modules.get(module_name)

        self.assertEqual(len(calls), 1)
        name, description, callback = calls[0]
        self.assertEqual(name, "OOFEM")
        self.assertIn("OOFEM", description)
        self.assertIs(callback, plugin_entry.initialize_plugin)
        self.assertIs(module_after, module_before)

    def test_registration_passes_packaged_icon_to_salome(self):
        calls = []
        icon = object()

        plugin_entry.register_plugin(lambda *args: calls.append(args), icon=icon)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], (
            "OOFEM",
            plugin_entry.PLUGIN_DESCRIPTION,
            plugin_entry.initialize_plugin,
        ))
        self.assertIs(calls[0][3], icon)

    def test_callback_activates_singleton_with_salome_context(self):
        context = object()

        class FakeModule:
            def __init__(self):
                self.contexts = []

            def activate(self, supplied_context):
                self.contexts.append(supplied_context)
                return "dock"

        fake_module = FakeModule()
        module_name = "OOFEMSalomePlugin.OOFEMModule"
        replacement = types.ModuleType(module_name)
        replacement.getModule = lambda: fake_module
        previous = sys.modules.get(module_name)
        sys.modules[module_name] = replacement
        try:
            result = plugin_entry.initialize_plugin(context)
        finally:
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous

        self.assertEqual(result, "dock")
        self.assertEqual(fake_module.contexts, [context])


if __name__ == "__main__":
    unittest.main()
