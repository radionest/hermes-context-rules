"""Deterministic enforce gates: run rule commands over the turn's changed files."""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .core import Rule  # noqa: F401  (type annotation)
from . import core

ENFORCE_TIMEOUT_S = 120

# (session_id, frozenset(changed_paths)) -> verdict message, so a nudge fired at
# attempt N is not re-run at attempt N+1 with identical inputs.
_VERDICT_CACHE: Dict[Tuple[str, Tuple[Tuple[str, int], ...]], Tuple[str, str]] = {}
_CACHE_LOCK = threading.Lock()


def _rules_with_enforce(base: Path) -> List[Rule]:
    return [r for r in core.load_rules(base) if r.enforce and r.mode == "glob"]


def _changed_fingerprint(paths: List[str]) -> Tuple[Tuple[str, int], ...]:
    entries = []
    for p in sorted(set(paths)):
        try:
            entries.append((p, os.stat(p).st_mtime_ns))
        except OSError:
            entries.append((p, 0))
    return tuple(entries)


def run_enforce(
    changed_paths: List[str],
    base: Optional[Path] = None,
    *,
    session_id: str = "",
    use_cache: bool = True,
) -> Optional[str]:
    """Run enforce commands for rules matching *changed_paths*.

    Returns a failure message for the agent (turn must not finish), or None when
    every gate passed / nothing matched. Never raises.
    """
    try:
        root_base = base if base is not None else core.session_cwd()
        paths = [p for p in changed_paths if isinstance(p, str) and p.strip()]
        if not paths:
            return None
        cache_key = (session_id or "default", _changed_fingerprint(paths))
        if use_cache:
            with _CACHE_LOCK:
                cached = _VERDICT_CACHE.get(cache_key)
            if cached is not None:
                return cached[1] or None

        failures: List[str] = []
        for rule in _rules_with_enforce(root_base):
            matched = [p for p in paths if core.rule_matches_path(rule, p, base=root_base)]
            if not matched:
                continue
            returncode, output = _run_rule_command(rule, matched)
            if returncode == 0:
                continue
            excerpt = (output or "").strip()
            if len(excerpt) > 1500:
                excerpt = excerpt[:1500] + "\n… [truncated]"
            failures.append(
                f"RULE '{rule.name}' ({rule.relpath}) enforce failed (exit {returncode}):\n"
                f"  command: {rule.enforce}\n  files: {', '.join(Path(m).name for m in matched)}\n"
                f"{indent(excerpt)}"
            )

        message = ("[context-rules] deterministic rule gates failed — fix and re-run:\n\n"
                   + "\n\n".join(failures)) if failures else None
        if use_cache:
            with _CACHE_LOCK:
                if len(_VERDICT_CACHE) > 64:
                    _VERDICT_CACHE.clear()
                _VERDICT_CACHE[cache_key] = ("", message or "")
        return message
    except Exception:
        return None  # a gate plugin must never break the turn


def indent(text: str, pad: str = "  ") -> str:
    return "\n".join(pad + line for line in text.splitlines())


def _run_rule_command(rule: Rule, matched: List[str]) -> Tuple[int, str]:
    env = dict(os.environ)
    root = str(rule.root)
    env["RULE_ROOT"] = root
    env["RULE_FILES"] = "\n".join(matched)
    argv = _shell_split(rule.enforce)
    files_as_args = any("$RULE_FILES" in part for part in argv)
    final_argv = [part.replace("$RULE_FILES", "\n".join(matched)) for part in argv]
    if not files_as_args:
        final_argv = final_argv + matched
    try:
        proc = subprocess.run(
            final_argv,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=ENFORCE_TIMEOUT_S,
        )
        output = (proc.stdout or "") + (("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or ""))
        return proc.returncode, output
    except subprocess.TimeoutExpired:
        return 124, f"enforce command timed out after {ENFORCE_TIMEOUT_S}s: {rule.enforce}"
    except FileNotFoundError:
        return 127, f"enforce command not found: {final_argv[0] if final_argv else rule.enforce}"
    except Exception as exc:
        return 1, f"enforce command failed to start: {exc}"


def _shell_split(command: str) -> List[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()
