"""Load the plugin through the REAL Hermes plugin machinery (isolated sandbox).

Builds a throwaway HERMES_HOME in the scratch dir, drops a symlink to this repo
as a user plugin, enables it via plugins.enabled, and runs discover_and_load.
Nothing in the live profile is touched.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

HERMES_SRC = Path(os.environ.get("HERMES_SRC", "/home/nest/.hermes/hermes-agent"))
assert (HERMES_SRC / "hermes_cli" / "plugins.py").exists(), f"Hermes source not found at {HERMES_SRC}"

sandbox = Path(tempfile.mkdtemp(prefix="ctx-rules-sandbox-", dir=os.environ.get("TMPDIR", "/tmp")))
(sandbox / "plugins").mkdir()
os.symlink(REPO, sandbox / "plugins" / "context-rules", target_is_directory=True)
(sandbox / "config.yaml").write_text(f"plugins:\n  enabled:\n    - context-rules\n")

os.environ["HERMES_HOME"] = str(sandbox)
sys.path.insert(0, str(HERMES_SRC))
sys.path.insert(0, str(REPO))

import hermes_cli.plugins as hp  # noqa: E402

hp.discover_plugins(force=True)
mgr = hp.get_plugin_manager()

loaded = {k: p for k, p in mgr._plugins.items() if "context-rules" in k}
assert loaded, f"context-rules not discovered; plugins={ {k: getattr(p, 'error', None) for k, p in mgr._plugins.items()} }"
for key, plugin in loaded.items():
    assert not getattr(plugin, "error", None), f"{key} load error: {plugin.error}"
    assert plugin.enabled, f"{key} not enabled"

mw = mgr._middleware.get("tool_execution", [])
assert mw, "tool_execution middleware not registered"

hooks_with = {name: cbs for name, cbs in mgr._hooks.items() if cbs}
assert "pre_verify" in hooks_with, f"pre_verify missing: {list(hooks_with)}"
assert "post_tool_call" in hooks_with, f"post_tool_call missing: {list(hooks_with)}"

sections = mgr._system_prompt_sections
assert "context.rules.manifest" in sections, f"prompt section missing: {list(sections)}"

print("OK: context-rules loads through the real Hermes loader")
print("  plugin keys:", list(loaded))
print("  middleware kinds:", {k: len(v) for k, v in mgr._middleware.items() if v})
print("  hooks:", sorted(hooks_with))
print("  prompt sections:", list(sections))
