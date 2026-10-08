## Why

`/context-rules sync` materializes the canonical `.cursor/rules/*.mdc` into exactly one consumer location — `.claude/rules/<name>.md` flat files. The operator now also runs the zcode client (z.ai) in the same workspaces; zcode reads workspace `AGENTS.md` and project rules under `.zcode/rules/` (per the operator's draft rules-tool spec, `~/.zcode/docs/rules-tool-spec.md`), so the canon needs a second projection target. This is pure projection mechanics: one canon, N consumers, generated output always marked and never treated as a source.

## What Changes

- Add a second sync target, `zcode`: the same cursor rules materialized as flat marked files under `<root>/.zcode/rules/<name>.md`, reusing the shared `projection_content()` header (GENERATED_MARKER + source relpath + apply-note).
- Add a managed digest block to the workspace `<root>/AGENTS.md` between `<!-- BEGIN:RULE-DIGESTS -->` / `<!-- END:RULE-DIGESTS -->` markers (marker-compatible with the operator's draft `rules_tool.py index` contract): one `- **<name>** — <summary>` line per projected rule, so the zcode agent discovers the rules. Only the marked block is ever rewritten; the rest of AGENTS.md is untouched. Missing AGENTS.md → created with just the digest section.
- Extend the CLI: `/context-rules sync [--target claude|zcode|all]` (default per decision D3 — backward compatibility preserved).
- Stale-file cleanup in `.zcode/rules/` mirrors the existing `.claude/rules` prune (canon-gone / duplicate rules), via a shared materialize helper so the write logic exists once.
- README: target table + flag documentation.

Non-goals: no change to rule semantics, mode resolution, enforce gates, injection middleware, or `load_rules` discovery (`.zcode/rules` is never a rule *source* for this plugin); no change to `sync-from-claude` import behavior (decision D4); no empirical verification that a live zcode client picks up the output (operator-session task).

## Capabilities

New Capabilities:
- `zcode-sync` — zcode target materialization, digest block management, stale cleanup, CLI targeting.

Modified Capabilities:
- None (no main specs exist yet; the sync CLI surface is specified for the first time inside `zcode-sync`).

## Impact

- `sync_rules.py` — refactor the per-root materialize loop into a shared helper parameterized by target dir; add zcode target dir, digest-block writer, zcode prune.
- `__init__.py` — parse `--target` for the `sync` subcommand; extend `_HELP`.
- `README.md` — targets table, flag docs.
- Tests: new `tests/test_sync_zcode.py` (creation / idempotency / stale-cleanup / marker / digest block / AGENTS.md safety).
- Base: stacked on branch `fix-review-findings` (uses its `projection_content()` helper); merge order: fix-review-findings first, then this change.
