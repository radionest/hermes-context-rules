# zcode-sync Spec Delta

## ADDED Requirements

### Requirement: Zcode target materialization
The sync operation SHALL materialize every canonical cursor rule of a rule
root as a flat markdown file under `<root>/.zcode/rules/<name>.md`, using the
same generated-projection header as the claude target (GENERATED_MARKER, the
canon relpath it projects, and an apply-note derived from the rule's mode and
globs). The projection body SHALL be the canon body verbatim. The zcode
target SHALL never be a rule source: discovery (`load_rules`) SHALL NOT read
`.zcode/rules/`.

#### Scenario: Sync creates the zcode projection
- **WHEN** sync runs with the zcode target enabled on a root whose
  `.cursor/rules/` holds one glob-mode .mdc
- **THEN** `<root>/.zcode/rules/<name>.md` exists, its head carries the
  GENERATED_MARKER with the projected .mdc relpath, and its body equals the
  .mdc body

#### Scenario: Zcode projections are idempotent
- **WHEN** sync runs twice with the zcode target enabled and no canon change
  happened in between
- **THEN** the second run reports every zcode projection as unchanged and
  rewrites no file (mtimes preserved)

#### Scenario: Zcode rules directory is not a source
- **WHEN** load_rules runs on a root that has `.zcode/rules/` files
- **THEN** none of those files appear in the returned rules (only
  `.cursor/rules` and `.claude/rules` remain rule sources)

### Requirement: Digest block in the workspace AGENTS.md
The zcode target SHALL maintain one digest block in `<root>/AGENTS.md`
delimited by `<!-- BEGIN:RULE-DIGESTS -->` and `<!-- END:RULE-DIGESTS -->`,
containing one `- **<name>** — <summary>` line per projected zcode rule,
where `<summary>` is the rule description when present and a
mode/glob-derived apply-note otherwise. The block SHALL be regenerated
idempotently between the markers; content outside the markers SHALL never be
modified. When `<root>/AGENTS.md` does not exist, sync SHALL create it
containing only a heading and the digest block. When the zcode target has no
rules to project, sync SHALL remove the digest lines, leaving the markers
(empty block) rather than stale entries.

#### Scenario: Digest block created in a missing AGENTS.md
- **WHEN** sync runs with the zcode target enabled and `<root>/AGENTS.md`
  does not exist
- **THEN** AGENTS.md is created with a heading and a digest block listing
  every projected rule as `- **<name>** — <summary>`

#### Scenario: Handwritten AGENTS.md content survives sync
- **WHEN** sync runs twice on an AGENTS.md that contains handwritten
  paragraphs before and after the digest markers
- **THEN** those paragraphs are byte-identical after both runs and only the
  lines between the markers ever change

#### Scenario: Digest tracks rule removal
- **WHEN** a canon .mdc is deleted and sync runs with the zcode target
  enabled
- **THEN** the digest block no longer lists the rule

### Requirement: Stale projection cleanup in the zcode target
The sync operation SHALL remove a file under `.zcode/rules/` when, and only
when, it carries the GENERATED_MARKER and its referenced canon .mdc (or, for
an unparsable reference, its matching canon stem) no longer exists, mirroring
the claude-target prune rules. A handwritten (unmarked) file in
`.zcode/rules/` SHALL never be removed or overwritten.

#### Scenario: Orphaned zcode projection is pruned
- **WHEN** a generated `.zcode/rules/<name>.md` references a .mdc that no
  longer exists on disk and sync runs
- **THEN** the projection is deleted and reported as removed

#### Scenario: Handwritten zcode rule is never touched
- **WHEN** `.zcode/rules/keep.md` is a handwritten (unmarked) file and sync
  runs with the zcode target enabled
- **THEN** the file exists afterwards with byte-identical content, and the
  collision is reported as skipped, not written

### Requirement: Target selection CLI
The sync subcommand SHALL accept `--target claude|zcode|all`. The default
(no flag) SHALL preserve the pre-change single-target behavior for
backward compatibility (decision D3: no-flag = claude only; `all` is always
explicit). `--target` with an unknown value SHALL produce a usage error
listing the accepted values; the sync report SHALL name the target(s) it
materialized.

#### Scenario: No flag keeps the old behavior
- **WHEN** `/context-rules sync` runs without arguments on a workspace that
  has no `.zcode/` directory
- **THEN** only `.claude/rules/` is materialized, no `.zcode/` directory or
  AGENTS.md digest is created, and the report matches the pre-change format
  plus the target name

#### Scenario: Explicit all materializes both targets
- **WHEN** `/context-rules sync --target all` runs on a workspace with canon
  rules
- **THEN** both `.claude/rules/` and `.zcode/rules/` projections plus the
  AGENTS.md digest block exist afterwards, and the report separates the two
  targets' created/updated/removed lines

#### Scenario: Unknown target value is a usage error
- **WHEN** `/context-rules sync --target vim` runs
- **THEN** sync materializes nothing and returns a one-line usage error
  naming the accepted values claude, zcode, all
