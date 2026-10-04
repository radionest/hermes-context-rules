"""Pytest bootstrap for a repo whose root doubles as the plugin package.

The Hermes loader imports this directory as ``hermes_plugins.<slug>`` (a
synthetic parent package), never via ``sys.path``. Pytest's Package collector
also imports the root ``__init__.py``, but derives its module name from the
directory (hyphenated -> not importable as a normal package, and no synthetic
parent -> relative imports inside the plugin fail).

Fix: pre-load the plugin package exactly the way the Hermes loader does and
register it under the directory name pytest will look up, so pytest's own
import returns the already-good module instead of re-executing it bare.
"""

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_PKG_NAME = _ROOT.name  # what pytest's resolve_pkg_root_and_module_name derives

if _PKG_NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _PKG_NAME, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
    _mod = importlib.util.module_from_spec(_spec)
    _mod.__package__ = _PKG_NAME
    _mod.__path__ = [str(_ROOT)]
    sys.modules[_PKG_NAME] = _mod
    _spec.loader.exec_module(_mod)

collect_ignore = ["__init__.py"]
