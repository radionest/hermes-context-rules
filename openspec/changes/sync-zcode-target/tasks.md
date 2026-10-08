# Tasks — sync-zcode-target

Base: worktree `.worktrees/sync-zcode-target`, branch `sync-zcode-target`
from `fix-review-findings` @ bc0aa10. Verify per group: `uv run --with pytest pytest tests/ -q`
(green = whole suite). Commits: conventional (`feat:`, `test:`, `docs:`, `refactor:`).

## 1. Refactor: shared materialize core

- [ ] 1.1 In `sync_rules.py`: extract the per-root loop body of `sync()` into `_materialize_root(root, cursor_rules, target_dir)` returning `(created, updated, removed, kept, skipped)`; `claude_rules_dir(root)` stays; `generated_files(root)` generalizes to `generated_files(rules_dir)` (takes a dir). Behavior-preserving: claude target output byte-identical. Files: `sync_rules.py`. Verify: `uv run --with pytest pytest tests/ -q` (existing suite green, zero fixture changes). Commit: `refactor: extract per-root materialize loop into _materialize_root`.
- [ ] 1.2 Commit `openspec/changes/sync-zcode-target/` artifacts onto the branch (D6, x-voice convention). Files: openspec change dir. Verify: `git status` clean for openspec/; `openspec validate sync-zcode-target --strict` passes. Commit: `docs: openspec artifacts for sync-zcode-target`.

## 2. Zcode target implementation

- [ ] 2.1 Add `zcode_rules_dir(root)` and target plumbing: `sync(explicit_cwd=None, target="claude")` — parse/validate `--target` in `__init__.py` (`claude|zcode|all`; unknown → one-line usage error naming accepted values, nothing materialized); report names the target(s) and separates per-target created/updated/removed lines. No flag = claude only (D3). Files: `sync_rules.py`, `__init__.py`. Verify: `uv run --with pytest pytest tests/ -q`. Commit: `feat: add --target flag to /context-rules sync (claude|zcode|all)`.
- [ ] 2.2 Digest writer `_write_digest(root, rules)`: maintain `- **<name>** — <summary>` lines between `<!-- BEGIN:RULE-DIGESTS -->` / `<!-- END:RULE-DIGESTS -->` in `<root>/AGENTS.md`; summary = rule description when non-empty else mode/glob apply-note; content outside markers byte-identical; missing file → created (heading + markers + lines); zero rules → markers stay, lines cleared; exactly one marker → skip + report, never auto-repair. Files: `sync_rules.py`. Verify: `uv run --with pytest pytest tests/test_sync_zcode.py -q` (task 4.2 tests). Commit: `feat: maintain zcode rule digest block in workspace AGENTS.md`.
- [ ] 2.3 Wire digest into the zcode target run: the zcode `_materialize_root` call also invokes `_write_digest` with the rules it wrote/kept (D2: one coherent operation; rules removed from canon → dropped from digest). Prune in `.zcode/rules/` mirrors `.claude/rules` (D5, shared code path). Files: `sync_rules.py`. Verify: `uv run --with pytest pytest tests/ -q`. Commit: `feat: wire digest writer into zcode sync target`.

## 3. CLI/help surface

- [ ] 3.1 `_HELP` text and `register_command` description: document `sync [--target claude|zcode|all]`, default claude, the digest-block contract, and the `.zcode/rules/` path. Files: `__init__.py`. Verify: `uv run --with pytest pytest tests/ -q` plus a help-content assert added to the task-4 tests (one line: `_HELP` mentions `--target`). Commit: `docs: document --target flag in /context-rules help`.

## 4. Tests: zcode target

- [ ] 4.1 Creation / idempotency / marker / source invariant: `.cursor/rules/*.mdc` (glob + always + desc modes) → sync target=all → both `.claude/rules/` and `.zcode/rules/` materialized, identical projection headers (GENERATED_MARKER + relpath); second sync = no-op (mtimes preserved, report says unchanged); `load_rules` never returns anything from `.zcode/rules/`; target=zcode alone leaves `.claude/rules/` untouched. Files: `tests/test_sync_zcode.py`. Verify: `uv run --with pytest pytest tests/test_sync_zcode.py -q`. Commit: `test: zcode target creation, idempotency, marker, source invariant`.
- [ ] 4.2 Digest block: created in missing AGENTS.md (heading + one line per rule); handwritten paragraphs before/after markers byte-identical after sync ×2; canon .mdc deleted → digest line dropped; single-marker file → skipped and file untouched; unknown `--target vim` → usage error, nothing written anywhere. Files: `tests/test_sync_zcode.py`. Verify: `uv run --with pytest pytest tests/test_sync_zcode.py -q`. Commit: `test: digest block creation, idempotency, safety, staleness`.
- [ ] 4.3 Stale cleanup in the zcode target: generated `.zcode/rules/x.md` whose referenced .mdc is gone → removed + digest line dropped; handwritten (unmarked) `.zcode/rules/keep.md` colliding with a canon rule name → never written/removed, reported skipped. Files: `tests/test_sync_zcode.py`. Verify: `uv run --with pytest pytest tests/test_sync_zcode.py -q`. Commit: `test: zcode stale-cleanup and handwritten-file safety`.

## 5. Docs

- [ ] 5.1 README: targets table (claude → `.claude/rules/*.md`, zcode → `.zcode/rules/*.md` + AGENTS.md digest block), `--target` flag + default claude, invariants (canon single source, generated marked, `.zcode/rules/` never a rule source), commit-or-ignore advice for `.zcode/rules/`. Files: `README.md`. Verify: read-back review; `uv run --with pytest pytest tests/ -q`. Commit: `docs: README target table and --target flag`.

## 6. Manual verification (implementer)

- [ ] 6.1 Fixture repo in `$TMPDIR/zcode-fixture`: git init, 2–3 `.cursor/rules/*.mdc` (glob/always/desc), invoke `sync` with target all (via a small driver script or the tests' invocation pattern), confirm both targets + AGENTS.md digest materialized; second run = no-op; delete one .mdc → re-sync removes the projection and the digest line. Record observed outputs in the PR body. Files: none committed (tmp fixture). Verify: outputs pasted into PR description. Commit: none — evidence only.

## 7. Operator session (NOT the implementer)

- [ ] 7.1 O1 — empirical zcode pickup: in the fixture repo (task 6.1) or any merged workspace, start the live zcode client and confirm the agent sees the digest block in AGENTS.md and can open a `.zcode/rules/<name>.md` it lists. Command: start zcode in `<fixture>`; prompt: «list the project rule digests from AGENTS.md and show the full text of `<rule-name>`». Expectation: the agent quotes digest lines and the rule body. No automation — human eyeballs the reply. Owner: operator.
