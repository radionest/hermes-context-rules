"""Rule storage (``/context-rules sync`` target state) and the sync itself."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from core import GENERATED_MARKER, Rule, find_rule_roots, load_rules, session_cwd


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
            header = f"{GENERATED_MARKER} from {rule.relpath} — do not edit here; edit the .mdc. -->"
            glob_note = (
                f"Apply when working with files matching: {', '.join(rule.globs)}."
                if rule.globs
                else "Apply when relevant."
            )
            content = f"{header}\n\n{glob_note}\n\n{rule.body}\n"
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

        # drop generated files whose .mdc disappeared
        for stale in generated_files(root):
            if stale.name not in wanted:
                stale.unlink()
                removed.append(stale.name)

    lines = [f"context-rules sync: roots={len(roots)} cursor-rules={len(cursor_rules)}"]
    for label, items in (("created", created), ("updated", updated), ("removed", removed), ("unchanged", kept)):
        if items:
            lines.append(f"  {label}: {', '.join(items)}")
    return "\n".join(lines)
