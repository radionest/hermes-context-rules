"""Rule storage (``/context-rules sync`` target state) and the sync itself."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .core import GENERATED_MARKER, Rule, find_rule_roots, load_rules, session_cwd


def _name_to_filename(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return slug or "rule"


def claude_rules_dir(root: Path) -> Path:
    return root / ".claude" / "rules"


def generated_files(rules_dir: Path) -> List[Path]:
    out: List[Path] = []
    if not rules_dir.is_dir():
        return out
    for f in sorted(rules_dir.glob("*.md")):
        try:
            head = f.read_text(encoding="utf-8-sig")[:400]
        except (OSError, UnicodeDecodeError):
            continue
        if GENERATED_MARKER in head:
            out.append(f)
    return out


_MODE_NOTES = {
    "always": "Apply unconditionally.",
    "manual": "Apply when explicitly asked for.",
}


def projection_content(relpath: str, globs: List[str], body: str, mode: str = "glob") -> str:
    """The marked .claude/rules/*.md projection of a canonical .mdc."""
    header = f"{GENERATED_MARKER} from {relpath} — do not edit here; edit the .mdc. -->"
    if mode == "glob" and globs:
        note = f"Apply when working with files matching: {', '.join(globs)}."
    else:
        note = _MODE_NOTES.get(mode, "Apply when relevant.")
    return f"{header}\n\n{note}\n\n{body}\n"


_PROJECTION_SRC_RE = re.compile(re.escape(GENERATED_MARKER) + r" from (.*?) — do not edit")


def _projection_ref(proj: Path) -> Optional[str]:
    """The canon relpath a projection's header references, if parsable."""
    try:
        head = proj.read_text(encoding="utf-8-sig")[:400]
    except (OSError, UnicodeDecodeError):
        return None
    m = _PROJECTION_SRC_RE.search(head)
    return m.group(1) if m else None


def _materialize_root(
    root: Path,
    cursor_rules: List[Rule],
    target_dir: Path,
) -> Tuple[List[str], List[str], List[str], List[str], List[str]]:
    """Write/prune one root's managed projections into *target_dir*.

    One managed projection per canonical .mdc, keyed by the file it projects;
    slug collisions are reported, not silently clobbered. A handwritten
    (unmarked) file is never overwritten — name collisions between the two
    sources coexist as distinct rules by design.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    created: List[str] = []
    updated: List[str] = []
    removed: List[str] = []
    kept: List[str] = []
    skipped: List[str] = []
    by_ref: Dict[str, Rule] = {}
    for rule in cursor_rules:
        if rule.root != root:
            continue
        by_ref[rule.relpath] = rule
    managed: Dict[str, Path] = {}  # canon relpath -> projection path
    by_file: Dict[str, str] = {}   # projection filename -> canon relpath
    for relpath in by_ref:
        target = target_dir / f"{_name_to_filename(by_ref[relpath].name)}.md"
        fname = target.name
        if fname in by_file:
            skipped.append(f"{fname} (name collision between {by_file[fname]} and {relpath})")
            continue
        by_file[fname] = relpath
        managed[relpath] = target
    # track which canon rules actually hold their managed projection file
    managed_written: Dict[str, bool] = {}
    for relpath, target in managed.items():
        rule = by_ref[relpath]
        content = projection_content(rule.relpath, rule.globs, rule.body, rule.mode)
        written = False
        if target.exists():
            try:
                existing = target.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                existing = None
            if existing != content:
                # never overwrite a handwritten (unmarked) .claude rule —
                # or any file we could not read (may be handwritten)
                if existing is None or GENERATED_MARKER not in existing[:400]:
                    reason = "unreadable (fix permissions, or delete it if it is a stale projection)" if existing is None \
                        else "handwritten — import or rename it via /context-rules sync-from-claude"
                    skipped.append(f"{target.name} (existing .claude rule with this name is {reason})")
                else:
                    target.write_text(content, encoding="utf-8")
                    updated.append(target.name)
                    written = True
            else:
                kept.append(target.name)
                written = True
        else:
            target.write_text(content, encoding="utf-8")
            created.append(target.name)
            written = True
        managed_written[relpath] = written

    # prune: a generated file is stale only when its canon .mdc file is
    # GONE from disk (existence check, not parse success — an unparseable
    # canon keeps its projection), or when it duplicates the managed
    # projection of the same canon AND that managed projection exists
    # (if the managed name is occupied by a handwritten file, a marked
    # projection elsewhere — e.g. a pre-rename source — is the canon's
    # only projection and must be kept)
    canon_stems: Set[str] = set()
    cursor_dir = root / ".cursor" / "rules"
    if cursor_dir.is_dir():
        canon_stems = {p.stem for p in cursor_dir.glob("*.mdc")}
    for stale in generated_files(target_dir):
        ref = _projection_ref(stale)
        if ref is not None:
            gone = not (root / ref).exists()
            duplicate = ref in managed and managed[ref] != stale and managed_written.get(ref, False)
        else:
            gone = stale.stem not in canon_stems
            duplicate = False
        if gone or duplicate:
            try:
                stale.unlink()
                removed.append(stale.name)
            except OSError:
                skipped.append(f"{stale.name} (could not remove the stale projection)")
    return created, updated, removed, kept, skipped


def sync(explicit_cwd: Optional[str] = None) -> str:
    """Materialize cursor rules as flat Claude Code rules; report what happened.

    Every cursor rule mode gets a projection (always/manual included), so a
    projection written by sync-from-claude stays refreshed. A handwritten
    (unmarked) .claude/rules file is never overwritten — name collisions
    between the two sources coexist as distinct rules by design.
    """
    base = session_cwd(explicit_cwd)
    roots = find_rule_roots(base)
    if not roots:
        return "No rule roots found (looked for .cursor/rules and .claude/rules from cwd to git root)."

    rules = load_rules(base)
    cursor_rules = [r for r in rules if r.source == "cursor"]
    created: List[str] = []
    updated: List[str] = []
    removed: List[str] = []
    kept: List[str] = []
    skipped: List[str] = []
    for root in roots:
        c, u, r, k, s = _materialize_root(root, cursor_rules, claude_rules_dir(root))
        created.extend(c)
        updated.extend(u)
        removed.extend(r)
        kept.extend(k)
        skipped.extend(s)

    lines = [f"context-rules sync: roots={len(roots)} cursor-rules={len(cursor_rules)}"]
    for label, items in (("created", created), ("updated", updated), ("removed", removed), ("unchanged", kept)):
        if items:
            lines.append(f"  {label}: {', '.join(items)}")
    if skipped:
        lines.append("  skipped (needs attention):")
        lines.extend(f"    - {s}" for s in skipped)
    return "\n".join(lines)
