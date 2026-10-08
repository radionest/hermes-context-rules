# Design — sync-zcode-target

## Context

`sync_rules.sync()` on branch `fix-review-findings` walks rule roots and, per
root, materializes `.cursor/rules/*.mdc` into `.claude/rules/<name>.md` flat
projections (`projection_content()`: GENERATED_MARKER header + mode note +
verbatim body), then prunes stale generated files. The write logic lives in
one 80-line inline loop inside `sync()`. The operator additionally runs the
zcode client (z.ai) in these workspaces; zcode has no auto-load of any
`.zcode/rules/` directory — its discovery surface is the workspace
`AGENTS.md` plus the operator's draft rules-tool spec (`~/.zcode/docs/
rules-tool-spec.md`), which designates `<proj>/.zcode/rules/` as the project
rules dir and `AGENTS.md` digest blocks delimited by
`<!-- BEGIN:RULE-DIGESTS -->` / `<!-- END:RULE-DIGESTS -->` (marker-compatible with
the draft `rules_tool.py index`).

## Goals / Non-Goals

Goals: one canon, two consumers; zero duplication of the projection write
logic; generated output always marked and never parsed back as a rule source;
handwritten files (`.zcode/rules/*.md` without marker, AGENTS.md outside
markers) never touched; operator decision points surfaced before tasks.

Non-Goals: dispatching zcode rules by glob (rules-tool v2 dispatcher);
changes to rule semantics, modes, enforce gates, injection, or discovery;
changes to sync-from-claude import behavior; empirical zcode pickup
verification (operator session).

## Decisions

Full list with options is in the operator gate block below (D1–D6); the
design assumes the operator-approved combination. Technical decisions that
follow from any approved combination:

- **Shared materialize core.** Extract the per-root loop body of `sync()`
  into `_materialize_root(root, rules, target_dir, *, write_digest=None)`
  returning `(created, updated, removed, kept, skipped)`; claude target =
  existing dir + no digest; zcode target = `.zcode/rules` + digest writer.
  One implementation of the projection write, collision policy, and prune
  rules (`sync_rules.py` stays the single home; `sync_from_claude.py` already
  imports `projection_content` from it).
- **Digest as part of the zcode target.** The digest block is written by the
  same `_materialize_root` call that writes `.zcode/rules/` (the digest is
  "how the zcode agent discovers the rules", not a separate feature), so
  `--target zcode` is one coherent operation.
- **Marker reuse.** Same GENERATED_MARKER and the same
  `"<marker> from <relpath> — do not edit here; edit the .mdc. -->"` header
  string via `projection_content()`; `_projection_ref` parses it back for
  prune. No zcode-specific marker format.
- **AGENTS.md safety.** Only lines strictly between the markers are
  replaced. If only one marker is present (malformed block), the file is
  left untouched and reported as skipped — never auto-repaired. Missing
  file → created with a heading + empty block; the heading is a fixed
  string so creation is deterministic.
- **No new dependencies.** stdlib only (re, pathlib); PyYAML stays optional.

## Risks / Trade-offs

- [Digest block conflicts with a future `rules_tool.py index` run] → both
  tools use the same marker pair and the same one-line-per-rule shape; the
  digest regenerates idempotently, and whichever runs last wins inside the
  markers only. Operator keeps rule digests in user-scope `~/.zcode/AGENTS.md`
  (different file) — no overlap with workspace AGENTS.md.
- [Two writers, one AGENTS.md (operator hand-edits inside markers)] →
  content between markers is by contract generated; operator convention is
  digests-only there. Mitigation: README documents the contract; malformed
  marker state → skip + report, never guess.
- [`.zcode/rules/` untracked in some repos] → README tells operators to
  commit or ignore it; sync never touches git config.
- [Refactor of `sync()` into `_materialize_root` churns reviewed code] →
  behavior-preserving; existing test suite (green on the branch) pins the
  claude-target behavior; new tests pin zcode-target behavior.

## Migration Plan

Single plugin repo, no deployment. Rollback = revert the branch commits;
generated `.zcode/rules/` files are inert without the plugin and can be
deleted by hand (`grep -l GENERATED_MARKER .zcode/rules/*.md`).

## Open Questions

None at design level; D1–D6 below are the operator gate.

---

## OPERATOR GATE — read before tasks.md

Stacked on `fix-review-findings` @ bc0aa10 (per operator choice, 2026-10-08).
The decisions below shape tasks and code; **tasks.md and code wait for
explicit approval of this block.**

**APPROVED 2026-10-08 (operator, via clarification): D1–D5 as recommended;
D6 per briefing (x-voice convention). Gate is closed — proceed to tasks.**

### D1 — целевой путь для zcode-проекций

`<root>/.zcode/rules/` — один вариант, зафиксированный черновиком спеки
правил zcode (`~/.zcode/docs/rules-tool-spec.md`, 2026-10-02):
  «Rules dirs: user `~/.zcode/rules/`, project `<proj>/.zcode/rules/`».
  У оператора там уже есть глобальные правила (~/.zcode/rules/*.md,
  8 файлов), т.е. путь конвентионен, не изобретён нами.
- Альтернатива `.agents/rules/` (кросс-клиентский стандарт) — в harness
  оператора не используется вовсе; добавили бы третий путь без потребителя.
- «Оба» — два пути с одинаковым содержимым без читателя у второго.

Рекомендация: `.zcode/rules/` (один путь, есть спека и живой референс).

### D2 — как zcode-агент узнаёт о правилах: digest-блок в AGENTS.md

Авто-загрузки `.zcode/rules/` в zcode нет. Спека правил zcode задаёт
дайджест-индекс в AGENTS.md между маркерами `<!-- BEGIN:RULE-DIGESTS -->` /
`<!-- END:RULE-DIGESTS -->` — тот же паттерн, что у оператора в
~/.zcode/AGENTS.md («Rule digests (full texts in ~/.zcode/rules/)»).
Sync ПРАВИТ только строки между маркерами; вне маркеров — никогда.
AGENTS.md отсутствует → создаётся с заголовком + блоком. Блок без правил →
маркеры остаются, строки стираются (не гниют).
- Альтернатива «индекс-файл .zcode/rules/INDEX.md» — не описана ни в одной
  спеке zcode-харнесса, зcode-агент его не ищет.
- «Sync не пишет digest, только документирует» — правила материализуются,
  но агент их не видит: таргет без обнаружения бесполезен.

Рекомендация: digest-блок между маркерами, marker-compatible с
rules-tool спекой.

### D3 — CLI: `--target claude|zcode|all`, дефолт = claude (старое поведение)

Без флага ровно прежнее поведение (только .claude/rules) — обратная
совместимость: ни один существующий вызов / workflow не начинает вдруг
писать в .zcode/ и AGENTS.md. `all` всегда явный.
- Альтернатива «дефолт all» — скрытая запись в AGENTS.md у существующих
  пользователей; ломает «sync ничего не создаёт вне .claude/rules».

Рекомендация: дефолт claude; `--target zcode|all` — opt-in.

### D4 — импорт-пайплайн sync-from-claude не затрагивается

`sync_from_claude.apply()` перезаписывает источник `.md` как проекцию
нового .mdc — это механика канона↔claude, не таргетная проекция. Zcode-таргет
получает правила при следующем `/context-rules sync --target zcode|all`.
Проекция при импорте в .zcode/ не пишется: apply не знает о таргетах,
и «импорт + мгновенный zcode-материализ» склеил бы две операции с разными
откатами (у apply свой rollback).

Рекомендация: оставить apply как есть; zcode-материализация — только sync.

### D5 — stale-cleanup в .zcode/rules/ зеркален .claude/rules

Те же правила prune (canon-gone / duplicate), тот же контракт «handwritten
никогда не трогаем». Это прямое следствие общего `_materialize_root`.

Рекомендация: зеркально, без исключений.

### D6 — openspec/changes/<name>/ трекать в git на ветке: да

x-voice-конвенция оператора. Ветки с change'ами смержатся — артефакты
поедут в master вместе с кодом; локальный стор без артефактов на ветке
теряет пропозал-контекст при мерже. Note: в ЭТОМ репо openspec/ сейчас
untracked в master (архив fix-sync-from-claude-review-findings тоже); на
нашей ветке коммитим openspec/changes/sync-zcode-target/ целиком.

Рекомендация: коммитить артефакты change'а на ветке (тот же PR).

### Операторская группа (не моя задача)

- O1: после мержа запустить живой zcode в fixture-репо и убедиться, что
  digest-блок действительно виден агенту (команда + ожидание — в tasks.md,
  operator-группа; zcode сам не запускаю).
