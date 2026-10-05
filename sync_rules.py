"""Rule storage (``/context-rules sync`` target state) and the sync itself."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from .core import GENERATED_MARKER, Rule, find_rule_roots, load_rules, session_cwd


def _name_to_filename(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return slug or "rule"


def claude_rules_dir(root: Path) -> Path:
    return root / ".claude" / "rules"


def generated_files(root: Path) -> List[Path]:
    out: List[Path] = []
    rules_dir = claude_rules_dir(root)
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


def projection_content(relpath: str, globs: List[str], body: str) -> str:
    """The marked .claude/rules/*.md projection of a canonical .mdc."""
    header = f"{GENERATED_MARKER} from {relpath} — do not edit here; edit the .mdc. -->"
    glob_note = (
        f"Apply when working with files matching: {', '.join(globs)}."
        if globs
        else "Apply when relevant."
    )
    return f"{header}\n\n{glob_note}\n\n{body}\n"


_PROJECTION_SRC_RE = re.compile(re.escape(GENERATED_MARKER) + r" from (.*?) — do not edit")


def _projection_is_stale(root: Path, proj: Path, canon_stems: set) -> bool:
    """A projection is stale when the .mdc it references no longer exists.

    The header carries the canon relpath (survives sync-from-claude renames,
    where the projection stem differs from the .mdc stem); fall back to a
    stem match for projections without a parsable source reference.
    """
    try:
        head = proj.read_text(encoding="utf-8-sig")[:400]
    except (OSError, UnicodeDecodeError):
        return False  # unreadable: leave it alone
    m = _PROJECTION_SRC_RE.search(head)
    if m:
        return not (root / m.group(1)).exists()
    return proj.stem not in canon_stems


def sync(explicit_cwd: Optional[str] = None) -> str:
    """Materialize glob/desc rules as flat Claude Code rules; report what happened."""
    base = session_cwd(explicit_cwd)
    roots = find_rule_roots(base)
    if not roots:
        return "No rule roots found (looked for .cursor/rules and .claude/rules from cwd to git root)."

    created, updated, removed, kept = [], [], [], []
    rules = load_rules(base)
    cursor_rules = [r for r in rules if r.source == "cursor" and r.mode in ("glob", "desc")]

    for root in roots:
        target_dir = claude_rules_dir(root)
        target_dir.mkdir(parents=True, exist_ok=True)
        wanted: Dict[str, Path] = {}
        for rule in cursor_rules:
            if rule.root != root:
                continue
            target = target_dir / f"{_name_to_filename(rule.name)}.md"
            wanted[target.name] = target
            content = projection_content(rule.relpath, rule.globs, rule.body)
            if target.exists():
                try:
                    existing = target.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    existing = None
                if existing != content:
                    target.write_text(content, encoding="utf-8")
                    updated.append(target.name)
                else:
                    kept.append(target.name)
            else:
                target.write_text(content, encoding="utf-8")
                created.append(target.name)

        # drop generated projections whose canon .mdc disappeared (any mode:
        # sync-from-claude marks the source .md of every imported rule)
        canon_stems = {p.stem for p in (root / ".cursor" / "rules").glob("*.mdc")} \
            if (root / ".cursor" / "rules").is_dir() else set()
        for stale in generated_files(root):
            if _projection_is_stale(root, stale, canon_stems):
                stale.unlink()
                removed.append(stale.name)

    lines = [f"context-rules sync: roots={len(roots)} cursor-rules={len(cursor_rules)}"]
    for label, items in (("created", created), ("updated", updated), ("removed", removed), ("unchanged", kept)):
        if items:
            lines.append(f"  {label}: {', '.join(items)}")
    return "\n".join(lines)
