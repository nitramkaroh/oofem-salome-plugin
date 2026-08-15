"""SALOME Python plugin-manager entry point.

SALOME discovers this file from a directory on ``SALOME_PLUGINS_PATH`` or
from one of its per-user plugin directories. Keep imports here deliberately
small: loading the plugin menu must not instantiate widgets or require SMESH.
"""

import os
import sys


source_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if source_dir not in sys.path:
    sys.path.insert(0, source_dir)

from OOFEMSalomePlugin.plugin_entry import register_plugin


register_plugin()
