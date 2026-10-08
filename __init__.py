"""context-rules — Cursor/Claude-compatible conditional project rules for Hermes.

Documented plugin surfaces only:
- register_middleware("tool_execution")  -> glob-rule injection into tool results
- register_hook("post_tool_call")        -> session-cwd tracking (for the prompt section)
- register_hook("pre_verify")            -> deterministic enforce gates
- register_system_prompt_section         -> manifest of always/desc rules for the session cwd
- register_command                       -> /context-rules
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core
from . import enforce as enforce_mod
from . import sync_rules
from . import sync_from_claude

logger = logging.getLogger(__name__)

# (Re)discover the session cwd when the prompt section renders; tools may have
# run in a different session than the one that rendered the section.
_CWD_LOCK = threading.Lock()
_LAST_CWD: Dict[str, str] = {}

_INJECTION_HEADER = "[context-rules] {name} ({relpath}) applies to this file:\n\n"
_INJECTION_REMINDER = "[context-rules] reminder: rule '{name}' ({relpath}) applies — /context-rules show {name}\n"


def _remember_cwd(session_id: str, workdir: Optional[str]) -> None:
    if not workdir:
        return
    with _CWD_LOCK:
        _LAST_CWD[session_id or "default"] = workdir


def _cwd_for(session_id: str, fallback: Optional[str] = None) -> Optional[str]:
    with _CWD_LOCK:
        return _LAST_CWD.get(session_id or "default", fallback)


# --------------------------------------------------------------------------
# middleware: inject glob rules into the first matching tool result


def _on_tool_execution(tool_name: str = "", args: Optional[Dict[str, Any]] = None,
                       session_id: str = "", next_call=None, **_: Any):
    result = next_call(args) if next_call is not None else None
    try:
        paths = core.candidate_paths(tool_name, args or {})
        if not paths:
            return result
        base = core.session_cwd(_cwd_for(session_id))
        matched = [
            r for r in core.rules_for_paths(paths, base=base)
            if r.mode == core.MODE_GLOB and r.body
        ]
        if not matched:
            return result
        additions: List[str] = []
        for rule in matched:
            if core.mark_injected(session_id, rule.key):
                body = rule.body
                if len(body) > core.DEFAULT_MAX_INJECT:
                    body = body[: core.DEFAULT_MAX_INJECT] + "\n… [rule truncated]"
                additions.append(_INJECTION_HEADER.format(name=rule.name, relpath=rule.relpath) + body)
            else:
                additions.append(_INJECTION_REMINDER.format(name=rule.name, relpath=rule.relpath))
        note = "\n".join(additions)
        if not note:
            return result
        if isinstance(result, str):
            return result + "\n\n" + note
        if isinstance(result, dict) and isinstance(result.get("output"), str):
            merged = dict(result)
            merged["output"] = result["output"] + "\n\n" + note
            return merged
        return result
    except Exception:
        logger.debug("context-rules injection failed", exc_info=True)
        return result


# --------------------------------------------------------------------------
# hooks


def _on_post_tool_call(tool_name: str = "", args: Optional[Dict[str, Any]] = None,
                       session_id: str = "", **_: Any) -> None:
    if tool_name == "terminal" and isinstance(args, dict):
        _remember_cwd(session_id, args.get("workdir"))


def _on_pre_verify(session_id: str = "", changed_paths: Optional[List[str]] = None,
                   attempt: int = 0, **_: Any) -> Optional[Dict[str, Any]]:
    if attempt > 2:  # self-throttle like the built-in nudge budget
        return None
    base_override = _cwd_for(session_id)
    message = enforce_mod.run_enforce(
        list(changed_paths or []),
        base=core.session_cwd(base_override) if base_override else None,
        session_id=session_id,
    )
    if message:
        return {"action": "continue", "message": message}
    return None


# --------------------------------------------------------------------------
# system prompt section


def _render_prompt_section(session_info: Dict[str, Any]) -> str:
    try:
        cwd_hint = session_info.get("cwd") or ""
        base = core.session_cwd(_cwd_for(str(session_info.get("session_id") or ""), cwd_hint) or cwd_hint)
        rules = core.load_rules(base)
        always = [r for r in rules if r.mode == core.MODE_ALWAYS]
        desc = [r for r in rules if r.mode in (core.MODE_DESC, core.MODE_MANUAL)]
        if not always and not desc:
            return ""
        parts: List[str] = [
            "Conditional project rules are active (.cursor/rules / .claude/rules).",
            "Glob-scoped rules attach automatically when you touch matching files.",
        ]
        for rule in always:
            body = rule.body
            if len(body) > core.MAX_ALWAYS_BODY_CHARS:
                body = body[: core.MAX_ALWAYS_BODY_CHARS] + "\n… [rule truncated]"
            header = f"RULE {rule.name} ({rule.relpath}) — always apply:"
            parts.append(f"{header}\n{body}" if body else header)
        if desc:
            lines = [
                f"- {r.name} ({r.relpath}): {r.description or 'no description'}"
                + (f" [enforce gate]" if r.enforce else "")
                for r in desc
            ]
            parts.append(
                "On-demand rules (read with /context-rules show <name> when relevant):\n"
                + "\n".join(lines)
            )
        text = "\n\n".join(parts)
        return text[: core.MAX_PROMPT_SECTION_CHARS]
    except Exception:
        logger.debug("context-rules prompt section failed", exc_info=True)
        return ""


# --------------------------------------------------------------------------
# slash command


def _handle_slash(raw_args: str) -> Optional[str]:
    argv = (raw_args or "").strip().split()
    sub = argv[0] if argv else "status"
    try:
        if sub in ("status", "list"):
            return _cmd_status(verbose=(sub == "status"))
        if sub == "show" and len(argv) >= 2:
            return _cmd_show(" ".join(argv[1:]))
        if sub == "sync":
            # parse/validate --target before anything materializes; any bare
            # token that is not --target/--target= is a usage error
            target = "claude"
            i = 1
            while i < len(argv):
                if argv[i] == "--target" and i + 1 < len(argv):
                    target = argv[i + 1]
                    i += 2
                elif argv[i].startswith("--target="):
                    target = argv[i].split("=", 1)[1]
                    i += 1
                else:
                    return "usage: /context-rules sync [--target claude|zcode|all]"
            if target not in sync_rules._TARGETS:
                return "usage: /context-rules sync [--target claude|zcode|all]"
            return sync_rules.sync(target=target)
        if sub == "sync-from-claude":
            # pass the raw remainder through: re-joining split() tokens
            # would collapse whitespace runs inside quoted rule names
            # (a rule literally named "a  b" must stay findable)
            rest = (raw_args or "").strip()[len("sync-from-claude"):]
            return sync_from_claude.handle(rest)
        if sub == "verify":
            return _cmd_verify()
        return _HELP
    except Exception as exc:
        return f"[context-rules] command failed: {exc}"


_HELP = """/context-rules — conditional project rules (.cursor/rules, .claude/rules)

  status            rules found for the current session cwd, by mode
  list              rule names only
  show <name>       full text of one rule
  sync [--target claude|zcode|all]
                    regenerate flat projections from .cursor/rules/*.mdc.
                    Default claude: .claude/rules/*.md only (pre-change behavior).
                    zcode: .zcode/rules/*.md plus a digest block in the workspace
                    AGENTS.md between <!-- BEGIN:RULE-DIGESTS --> and
                    <!-- END:RULE-DIGESTS --> (one "- **name** — summary" line per
                    rule; only lines between the markers are ever rewritten).
                    all: both targets, reports separated per target.
  sync-from-claude  import handwritten .claude/rules/*.md into .cursor/rules/*.mdc (propose/apply)
  verify            run enforce gates now over files changed this turn
"""


def _cmd_status(verbose: bool = True) -> str:
    base = core.session_cwd()
    rules = core.load_rules(base)
    if not rules:
        return f"No rules found under {base} (looked for .cursor/rules/*.mdc and .claude/rules/*.md up to git root)."
    lines: List[str] = [f"{len(rules)} rule(s) for {base}:"]
    for rule in rules:
        mode_labels = {
            core.MODE_ALWAYS: "always",
            core.MODE_GLOB: "glob",
            core.MODE_DESC: "on-demand",
            core.MODE_MANUAL: "manual",
        }
        line = f"  [{mode_labels[rule.mode]:>8}] {rule.name}  ({rule.relpath})"
        if rule.globs:
            line += f"  globs: {', '.join(rule.globs)}"
        if rule.enforce:
            line += "  ENFORCE: " + rule.enforce
        lines.append(line)
    return "\n".join(lines)


def _cmd_show(name: str) -> str:
    base = core.session_cwd()
    for rule in core.load_rules(base):
        if rule.name == name or rule.path.name == name:
            header = (
                f"RULE {rule.name} ({rule.relpath})\n"
                f"  mode: {rule.mode}"
                + (f" | globs: {', '.join(rule.globs)}" if rule.globs else "")
                + (f"\n  enforce: {rule.enforce}" if rule.enforce else "")
                + (f"\n  description: {rule.description}" if rule.description else "")
            )
            return header + "\n\n" + rule.body
    return f"No rule named '{name}' (see /context-rules list)."


def _cmd_verify() -> str:
    return ("Enforce gates run automatically at pre_verify over the files changed in the turn "
            "(changed_paths payload). There is no standalone file list to verify against here; "
            "edit files in a turn and finish it, or run the rule's enforce command directly.")


def register(ctx) -> None:
    ctx.register_middleware("tool_execution", _on_tool_execution)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("pre_verify", _on_pre_verify)
    try:
        ctx.register_system_prompt_section(
            "context.rules.manifest", _render_prompt_section, position="after_memory",
        )
    except Exception:
        logger.debug("system prompt section unavailable", exc_info=True)
    ctx.register_command(
        "context-rules", handler=_handle_slash,
        description="Conditional project rules (.cursor/rules, .claude/rules, .zcode/rules): status, show, sync [--target claude|zcode|all], sync-from-claude, verify.",
    )
