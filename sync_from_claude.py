"""``/context-rules sync-from-claude``: import handwritten .claude/rules/*.md
into the canonical .cursor/rules/*.mdc format.

Two phases, no in-band interactivity (works on every surface):
- ``propose`` — read-only plan. Per rule: ready | ask | conflict | imported.
- ``apply``   — write one .mdc (body verbatim). Unknowns come in as arguments:
  ``apply <name> [always | glob <globs...> | desc <text...> | manual] [--force] [--as <new>] [--root <dir>]``.
  The agent running the command asks the user the ``ask``/``conflict`` questions
  out of band and then calls apply with the answers.

The source .md is rewritten in place as a generated projection
(GENERATED_MARKER) of the new .mdc, closing the loop immediately: .cursor is
the single source of truth, .claude holds projections only.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .core import GENERATED_MARKER, _as_glob_list, _parse_frontmatter, find_rule_roots
from .sync_rules import projection_content

_MODES = ("always", "glob", "desc", "manual")
# keys that carry mode semantics; everything else is opaque carry-over
_MODE_KEYS = ("alwaysApply", "globs", "description")


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


def _mode_of_meta(meta: Dict[str, object]) -> str:
    """Resolve a mode from frontmatter markers (no claude-source default)."""
    if meta.get("alwaysApply") is True:
        return "always"
    if _as_glob_list(meta.get("globs")):
        return "glob"
    if str(meta.get("description") or "").strip():
        return "desc"
    return "manual"


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
                continue  # marked as a projection of some .mdc (kept even if that .mdc is missing; sync prunes orphans)
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
    name_counts: Dict[str, int] = {}
    for item in items:
        name_counts[item.name] = name_counts.get(item.name, 0) + 1
    for item in items:
        status, detail = item.status()
        if name_counts[item.name] > 1:
            try:
                root_label = item.root.relative_to(base).as_posix()
            except ValueError:
                root_label = item.root.as_posix()
            detail += f" [root: {root_label} — disambiguate with --root]"
        by_status.setdefault(status, []).append((item, detail))
    lines = [f"sync-from-claude propose: {len(items)} importable rule(s) for {base}"]
    for status in ("ready", "ask", "conflict", "imported"):
        for item, detail in by_status.get(status, []):
            lines.append(f"  [{status:>8}] {item.name}  ({item.path.relative_to(item.root).as_posix()})")
            lines.append(f"            {detail}")
    lines.append(
        "apply with: /context-rules sync-from-claude apply <name> [always | glob <globs...> | desc <text...> | manual] [--force] [--as <new-name>] [--root <dir>]"
    )
    return "\n".join(lines)


def _emit(front_lines: List[str], key: str, value: object) -> None:
    if isinstance(value, bool):
        front_lines.append(f"{key}: {str(value).lower()}")
    elif isinstance(value, str):
        front_lines.append(f"{key}: {json.dumps(value)}")
    elif value is None:
        return  # never stringify a null into the canon
    elif isinstance(value, (int, float)):
        front_lines.append(f"{key}: {value}")
    else:  # list, dict, date, ... — opaque carry-over; keep it re-parseable
        front_lines.append(f"{key}: {json.dumps(value, default=str)}")


def _carryable(mode: str, key: str) -> bool:
    """May *key* ride along without flipping the chosen mode's resolution?"""
    if key in _MODE_KEYS:
        return False
    return True


def apply(base: Optional[Path], name: str, *args: str) -> str:
    """Import one rule. Unknowns arrive as arguments (see module docstring)."""
    root_dir = base if base is not None else _cwd()
    mode: Optional[str] = None
    rest: List[str] = []
    force = False
    rename: Optional[str] = None
    root_sel: Optional[str] = None
    it = iter(args)
    for tok in it:
        if tok == "--force":
            force = True
        elif tok == "--as":
            rename = next(it, None)
            if rename is None:
                return "usage: --as needs a value: apply <name> [mode...] [--as <new-name>]"
        elif tok == "--root":
            root_sel = next(it, None)
            if root_sel is None:
                return "usage: --root needs a value: apply <name> [mode...] [--root <dir>]"
        elif mode is None and tok in _MODES:
            mode = tok
        else:
            rest.append(tok)
    if rename is not None:
        # the canon is a cross-platform format: reject every path separator on
        # every OS (a "\\" name would break a Windows checkout and vice versa)
        seps = {"/", "\\"} | {os.sep, os.altsep} - {None, ""}
        if (not rename.strip() or any(sep in rename for sep in seps)
                or rename.strip() != rename or rename in (".", "..")
                or rename.startswith("-")):
            return f"[context-rules] invalid --as name: {rename!r}"
    mode_given = mode is not None

    matches = [i for i in _collect(root_dir) if i.name == name]
    if root_sel is not None:
        sel = Path(root_sel).expanduser()
        if not sel.is_absolute():
            sel = root_dir / sel
        sel = sel.resolve()
        matches = [i for i in matches if i.root == sel]
        if not matches:
            return (f"No importable rule named '{name}' under root {sel} "
                    f"(see /context-rules sync-from-claude propose).")
    if len(matches) > 1:
        roots = "\n".join(f"  --root {i.root}" for i in matches)
        return (f"Rule name '{name}' exists in {len(matches)} rule roots — disambiguate:\n{roots}")
    item = matches[0] if matches else None
    if item is None:
        return (f"No importable rule named '{name}' (see /context-rules sync-from-claude propose; "
                f"generated projections are skipped).")

    if mode is None:
        if not item.has_meta:
            # an identical-body canon may still allow the fix-up below
            if item.target_exists() and item.target_body() == item.body.strip():
                pass  # fall through to the fix-up gate
            else:
                return (f"Rule '{name}' has no frontmatter — a mode is required: "
                        f"apply {name} always | glob <globs...> | desc <text...> | manual")
        else:
            mode, _ = item._mode_from_meta()

    target = item.target if rename is None else item.root / ".cursor" / "rules" / f"{rename}.mdc"
    target_existed = target.exists()
    existing_body: Optional[str] = None
    canon_meta: Dict[str, object] = {}
    if target_existed:
        try:
            text = target.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            text = None
        if text is not None:
            canon_meta, body = _parse_frontmatter(text)
            existing_body = body.strip() or None

    # 'imported' fix-up: identical body and no conflicting arguments — the
    # canon already holds this rule; only rewrite the source .md as its
    # projection, deriving everything from the authoritative .mdc. An
    # explicit mode equal to the canon's resolved mode is not a conflict.
    body_identical = existing_body is not None and existing_body == item.body.strip()
    mode_matches = not mode_given or (existing_body is not None and mode == _mode_of_meta(canon_meta))
    if (body_identical and mode_matches and not rest and rename is None
            and (not force or not mode_given)):
        proj_mode = _mode_of_meta(canon_meta)
        proj_globs = _as_glob_list(canon_meta.get("globs"))
        src = item.path.relative_to(item.root).as_posix()
        try:
            source_backup = item.path.read_bytes()
            item.path.write_text(
                projection_content(target.relative_to(item.root).as_posix(), proj_globs,
                                   item.body.strip(), proj_mode),
                encoding="utf-8",
            )
        except OSError as exc:
            _restore_bytes(item.path, source_backup)  # best effort; no canon was touched
            return f"[context-rules] failed to rewrite the source .md ({exc}); canon left unchanged."
        return f"[context-rules] {name}: canon unchanged; source .md rewritten as a projection: {src}"

    if mode is None:
        return (f"Rule '{name}' has no frontmatter — a mode is required: "
                f"apply {name} always | glob <globs...> | desc <text...> | manual")

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
            meta_desc = item.meta.get("description")
            desc = meta_desc.strip() if isinstance(meta_desc, str) else ""
            if not desc:
                return f"desc mode needs a description: apply {name} desc <text...>"
        else:
            desc = " ".join(rest)
        front_lines.append(f"description: {json.dumps(desc)}")
    else:  # manual
        # explicit override of an alwaysApply source: record the flip
        if item.meta.get("alwaysApply") is True:
            front_lines.append("alwaysApply: false")

    # Carry over metadata without changing the chosen mode's resolution.
    # mode keys: only the ones emitted above; always-mode keeps source globs
    # (inert under alwaysApply: true, preserved for a later flip); desc-mode
    # drops globs (they would win over description on re-read); manual takes
    # no mode keys at all. Canon-only keys survive a rebuild (--force).
    carry: Dict[str, object] = {}
    for key, value in item.meta.items():
        if not _carryable(mode, key) or value is None or key in carry:
            continue
        carry[key] = value
    if mode == "always" and "globs" not in carry:
        src_globs = item.meta.get("globs")
        if src_globs is not None:
            carry["globs"] = src_globs
    for key, value in canon_meta.items():
        if key in item.meta or not _carryable(mode, key) or value is None or key in carry:
            continue
        carry[key] = value
    for key, value in carry.items():
        _emit(front_lines, key, value)

    if target_existed and existing_body != item.body.strip() and not force:
        return (f"conflict: {target.relative_to(item.root).as_posix()} exists with a different body — "
                f"re-run with --force to overwrite or --as <new-name> to import under a new name")

    if not item.body.strip():
        return f"Rule '{name}' has an empty body — nothing to import."
    content = "---\n" + "\n".join(front_lines) + "\n---\n\n" + item.body.strip() + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    original_bytes: Optional[bytes] = None
    if target_existed:
        try:
            original_bytes = target.read_bytes()
        except OSError:
            original_bytes = None  # unreadable pre-existing canon: keep it, never unlink
    source_backup: Optional[bytes] = None
    try:
        source_backup = item.path.read_bytes()
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        # the canon write failed mid-flight: restore whatever pre-existed so no
        # half-imported state stays (the source .md was not touched yet)
        _restore_bytes(target, original_bytes if target_existed else None)
        return f"[context-rules] failed to write the canon .mdc ({exc}); nothing was imported."

    # Rewrite the source .md as a marked projection of the new .mdc: the canon
    # is authoritative now and the rule must never be counted twice. If this
    # second write fails, roll the canon back so no half-imported state stays
    # (exception: an unreadable pre-existing canon is left as written — it is
    # kept, never unlinked — and an unreadable .md is not loaded anyway).
    try:
        item.path.write_text(
            projection_content(target.relative_to(item.root).as_posix(),
                               globs if mode == "glob" else [], item.body.strip(), mode),
            encoding="utf-8",
        )
    except OSError as exc:
        rollback_failed = False
        try:
            _restore_bytes(item.path, source_backup)
            if original_bytes is not None:
                target.write_bytes(original_bytes)
            elif not target_existed:
                # re-check existence/content so a concurrent import that
                # finished in the meantime is never clobbered by our rollback
                current: Optional[str] = None
                try:
                    current = target.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    current = None
                if current == content:
                    target.unlink(missing_ok=True)
        except OSError:
            rollback_failed = True
        if rollback_failed:
            status = "rollback failed — inspect the canon and the source .md manually"
        elif original_bytes is not None:
            status = "canon restored"
        elif target_existed:
            status = "pre-existing canon left as written (it was unreadable before the import)"
        else:
            status = "new canon removed"
        return (f"[context-rules] import failed while rewriting the source .md ({exc}); {status}.")

    where = target.relative_to(item.root).as_posix()
    src = item.path.relative_to(item.root).as_posix()
    return (f"[context-rules] imported {name} -> {where} (mode={mode}); "
            f"source .md rewritten as a projection: {src}")


def _cwd() -> Path:
    from .core import session_cwd

    return session_cwd()


def _restore_bytes(path: Path, backup: Optional[bytes]) -> None:
    """Best-effort restore of a file's pre-write content (None never unlinks)."""
    if backup is None:
        return
    try:
        path.write_bytes(backup)
    except OSError:
        pass


def handle(raw_args: str) -> str:
    """Slash-command entry point: ``sync-from-claude [apply <name> [mode...] [--force] [--as <n>] [--root <dir>]]``."""
    raw = (raw_args or "").strip()
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        return f"[context-rules] cannot parse arguments ({exc}); quote names containing spaces."
    if not argv or argv[0] == "propose":
        return propose()
    if argv[0] == "apply":
        if len(argv) < 2:
            return "usage: /context-rules sync-from-claude apply <name> [always | glob <globs...> | desc <text...> | manual] [--force] [--as <new-name>] [--root <dir>]"
        return apply(None, argv[1], *argv[2:])
    return _HELP


_HELP = """sync-from-claude — import handwritten .claude/rules/*.md into .cursor/rules/*.mdc (the canon)

  sync-from-claude                propose: read-only plan (ready | ask | conflict | imported)
  sync-from-claude apply <name> [mode] [--force] [--as <new-name>] [--root <dir>]
      mode: always | glob <globs...> | desc <text...> | manual
      --force   overwrite an existing .mdc with a different body
      --as      import under a new .mdc name (resolve a conflict by renaming)
      --root    pick the rule root when the same name exists in several roots

Generated projections (marked files) are skipped when proposing imports.
The source .md is rewritten as a generated projection of the new .mdc.
"""
