"""``/context-rules sync-from-claude``: import handwritten .claude/rules/*.md
into the canonical .cursor/rules/*.mdc format.

Two phases, no in-band interactivity (works on every surface):
- ``propose`` — read-only plan. Per rule: ready | ask | conflict | imported.
- ``apply``   — write one .mdc (body verbatim). Unknowns come in as arguments:
  ``apply <name> [always | glob <globs...> | desc <text...> | manual] [--force] [--as <new>]``.
  The agent running the command asks the user the ``ask``/``conflict`` questions
  out of band and then calls apply with the answers.

The source .md is rewritten in place as a generated projection
(GENERATED_MARKER) of the new .mdc, closing the loop immediately: .cursor is
the single source of truth, .claude holds projections only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .core import GENERATED_MARKER, _as_glob_list, _parse_frontmatter, find_rule_roots
from .sync_rules import projection_content

_MODES = ("always", "glob", "desc", "manual")


class _Importable:
    """One .claude/rules/*.md evaluated against the canon."""

    def __init__(self, path: Path, root: Path):
        self.path = path
        self.root = root
        self.name = path.stem
        text = path.read_text(encoding="utf-8-sig")
        self.meta, self.body = _parse_frontmatter(text)
        self.target = root / ".cursor" / "rules" / f"{self.name}.mdc"

    @property
    def has_meta(self) -> bool:
        return bool(self.meta)

    def target_exists(self) -> bool:
        return self.target.exists()

    def target_body(self) -> Optional[str]:
        if not self.target.exists():
            return None
        try:
            _, body = _parse_frontmatter(self.target.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError):
            return None
        return body.strip()

    def status(self) -> Tuple[str, str]:
        """(status, one-line detail) — ready | ask | conflict | imported."""
        if self.target_exists():
            if self.target_body() == self.body.strip():
                return "imported", "identical .mdc exists — re-run apply to rewrite the source .md as a projection"
            return "conflict", f".cursor/rules/{self.name}.mdc exists with a different body (use --force to overwrite, --as to rename)"
        if not self.has_meta:
            unknowns = ["mode (always | glob <globs...> | desc <text...> | manual)"]
            return "ask", "missing frontmatter — unknown: " + "; ".join(unknowns)
        mode, detail = self._mode_from_meta()
        return "ready", detail or f"mode={mode}"

    def _mode_from_meta(self) -> Tuple[str, str]:
        if self.meta.get("alwaysApply") is True:
            return "always", "alwaysApply: true"
        globs = _as_glob_list(self.meta.get("globs"))
        if globs:
            return "glob", "globs: " + ", ".join(globs)
        desc = str(self.meta.get("description") or "").strip()
        if desc:
            return "desc", f"description: {desc}"
        return "manual", "frontmatter present, no mode markers -> manual"


def _collect(base: Path) -> List[_Importable]:
    out: List[_Importable] = []
    for root in find_rule_roots(base):
        rules_dir = root / ".claude" / "rules"
        if not rules_dir.is_dir():
            continue
        for f in sorted(rules_dir.glob("*.md")):
            try:
                head = f.read_text(encoding="utf-8-sig")[:400]
            except (OSError, UnicodeDecodeError):
                continue
            if GENERATED_MARKER in head:
                continue  # our own sync projection; canon already exists
            try:
                out.append(_Importable(f, root))
            except (OSError, UnicodeDecodeError):
                continue
    return out


def propose(explicit: Optional[Path] = None) -> str:
    """Read-only import plan for .claude/rules/*.md not yet in the canon."""
    base = explicit if explicit is not None else _cwd()
    items = _collect(base)
    if not items:
        return "No importable .claude/rules/*.md found (generated projections and files already in the canon are skipped)."
    by_status: Dict[str, List[Tuple[_Importable, str]]] = {}
    for item in items:
        status, detail = item.status()
        by_status.setdefault(status, []).append((item, detail))
    lines = [f"sync-from-claude propose: {len(items)} importable rule(s) for {base}"]
    for status in ("ready", "ask", "conflict", "imported"):
        for item, detail in by_status.get(status, []):
            lines.append(f"  [{status:>8}] {item.name}  ({item.path.relative_to(item.root).as_posix()})")
            lines.append(f"            {detail}")
    lines.append(
        "apply with: /context-rules sync-from-claude apply <name> [always | glob <globs...> | desc <text...> | manual] [--force] [--as <new-name>]"
    )
    return "\n".join(lines)


def apply(base: Optional[Path], name: str, *args: str) -> str:
    """Import one rule. Unknowns arrive as arguments (see module docstring)."""
    root_dir = base if base is not None else _cwd()
    mode: Optional[str] = None
    rest: List[str] = []
    force = False
    rename: Optional[str] = None
    it = iter(args)
    for tok in it:
        if tok == "--force":
            force = True
        elif tok == "--as":
            rename = next(it, None)
            if rename is None:
                return "usage: --as needs a value: apply <name> [mode...] [--as <new-name>]"
        elif mode is None and tok in _MODES:
            mode = tok
        else:
            rest.append(tok)
    if rename is not None and (not rename.strip() or "/" in rename or rename.strip() != rename or rename.startswith("-")):
        return f"[context-rules] invalid --as name: {rename!r}"

    items = {i.name: i for i in _collect(root_dir)}
    item = items.get(name)
    if item is None:
        return (f"No importable rule named '{name}' (see /context-rules sync-from-claude propose; "
                f"generated projections are skipped).")

    if mode is None:
        if not item.has_meta:
            return (f"Rule '{name}' has no frontmatter — a mode is required: "
                    f"apply {name} always | glob <globs...> | desc <text...> | manual")
        mode, _ = item._mode_from_meta()

    front_lines: List[str] = []
    globs: List[str] = []
    if mode == "always":
        front_lines.append("alwaysApply: true")
    elif mode == "glob":
        if not rest:
            globs = _as_glob_list(item.meta.get("globs"))
            if not globs:
                return f"glob mode needs globs: apply {name} glob <globs...>"
        else:
            globs = list(rest)
        front_lines.append("globs: [" + ", ".join(json.dumps(g) for g in globs) + "]")
    elif mode == "desc":
        if not rest:
            desc = str(item.meta.get("description") or "").strip()
            if not desc:
                return f"desc mode needs a description: apply {name} desc <text...>"
        else:
            desc = " ".join(rest)
        front_lines.append(f"description: {json.dumps(desc)}")
    else:  # manual
        if item.meta.get("alwaysApply") is True:
            front_lines.append("alwaysApply: false")
    # carry over non-mode keys from the source frontmatter (e.g. enforce)
    for key, value in item.meta.items():
        if key in ("alwaysApply", "globs", "description"):
            continue
        if isinstance(value, bool):
            front_lines.append(f"{key}: {str(value).lower()}")
        elif isinstance(value, str):
            front_lines.append(f"{key}: {json.dumps(value)}")
        else:
            front_lines.append(f"{key}: {value}")

    target = item.target if rename is None else item.root / ".cursor" / "rules" / f"{rename}.mdc"
    if target.exists() and not force:
        try:
            _, existing_body = _parse_frontmatter(target.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError):
            existing_body = None
        if (existing_body or "").strip() != item.body.strip():
            return (f"conflict: {target.relative_to(item.root).as_posix()} exists with a different body — "
                    f"re-run with --force to overwrite or --as <new-name> to import under a new name")

    if not item.body.strip():
        return f"Rule '{name}' has an empty body — nothing to import."
    content = "---\n" + "\n".join(front_lines) + "\n---\n\n" + item.body.strip() + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    # Rewrite the source .md as a marked projection of the new .mdc: the canon
    # is authoritative now and the rule must never be counted twice.
    item.path.write_text(
        projection_content(target.relative_to(item.root).as_posix(), globs, item.body.strip()),
        encoding="utf-8",
    )

    where = target.relative_to(item.root).as_posix()
    src = item.path.relative_to(item.root).as_posix()
    return (f"[context-rules] imported {name} -> {where} (mode={mode}); "
            f"source .md rewritten as a projection: {src}")


def _cwd() -> Path:
    from .core import session_cwd

    return session_cwd()


def handle(raw_args: str) -> str:
    """Slash-command entry point: ``sync-from-claude [apply <name> [mode...] [--force] [--as <n>]]``."""
    argv = (raw_args or "").strip().split()
    if not argv or argv[0] == "propose":
        return propose()
    if argv[0] == "apply":
        if len(argv) < 2:
            return "usage: /context-rules sync-from-claude apply <name> [always | glob <globs...> | desc <text...> | manual] [--force] [--as <new-name>]"
        return apply(None, argv[1], *argv[2:])
    return _HELP


_HELP = """sync-from-claude — import handwritten .claude/rules/*.md into .cursor/rules/*.mdc (the canon)

  sync-from-claude                propose: read-only plan (ready | ask | conflict | imported)
  sync-from-claude apply <name> [mode] [--force] [--as <new-name>]
      mode: always | glob <globs...> | desc <text...> | manual
      --force   overwrite an existing .mdc with a different body
      --as      import under a new .mdc name (resolve a conflict by renaming)

Generated projections (marked files) are skipped: their canon already exists.
The source .md is rewritten as a generated projection of the new .mdc.
"""
