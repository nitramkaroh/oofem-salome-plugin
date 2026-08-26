"""Register OOFEM in a generated SALOME application launcher."""

import os
import re


def _modules_with_oofem_first(value):
    modules = [
        item.strip()
        for item in re.split(r"[:,;]", value or "")
        if item.strip() and item.strip() != "OOFEM"
    ]
    modules.insert(0, "OOFEM")
    return ",".join(modules)


def init(context, root_dir):
    """Add OOFEM after SALOME has initialized its built-in environment."""
    module_root = os.path.join(root_dir, "INSTALL", "OOFEM")
    python_root = os.path.join(module_root, "bin", "salome")
    resource_root = os.path.join(
        module_root, "share", "salome", "resources", "oofem"
    )

    context.setVariable("OOFEM_ROOT_DIR", module_root, overwrite=True)
    context.addToPath(python_root)
    context.addToPythonPath(python_root)
    modules = _modules_with_oofem_first(os.environ.get("SALOME_MODULES", ""))
    context.setVariable("SALOME_MODULES", modules, overwrite=True)
    context.addToVariable("SalomeAppConfig", resource_root, separator=":")
