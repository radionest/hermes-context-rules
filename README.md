# hermes-context-rules

Cursor- и Claude-совместимые условные правила проекта для Hermes Agent.

Один источник истины — `.cursor/rules/*.mdc` (формат Cursor: frontmatter
`description` / `globs` / `alwaysApply`). Cursor читает их нативно;
этот плагин даёт Hermes ту же условную семантику + то, чего нет ни у Cursor,
ни у Claude Code — **детерминированный enforcement**.

## Проблема, которую решает

- `AGENTS.md` грузится целиком, всегда, без условий — и модель может его
  проигнорировать: это advisory-текст.
- Описание детерминированных проверок (линтеры, гейты) раздувает AGENTS.md,
  хотя это код, а не проза.

## Что делает плагин

| Тип правила (frontmatter) | Поведение в Hermes |
|---|---|
| `alwaysApply: true` | Полный текст — в системный промпт (секция `context.rules.manifest`, рендер по cwd сессии) |
| `globs: src/**/*.py` | Авто-инжекция полного текста в результат первого tool-вызова, затрагивающего матчащийся файл (read_file / write_file / patch / search_files / terminal workdir). Повторно — одна строка-напоминание |
| `description:` (без globs) | Компактный индекс в промпте: имя + description + путь; агент читает файл по необходимости |
| ничего (manual) | Только по `/context-rules show <name>` |
| `enforce: <shell cmd>` (расширение, Cursor игнорирует) | Гейт: команда запускается на `pre_verify` для изменённых за ход файлов, матчащих `globs`. Ненулевой exit → агент обязан исправить (turn не завершается). Аналог Claude Code Stop-hook, генерализация text_lint_gate |

Файлы правил могут меняться в течение сессии: кэш инвалидируется по mtime.

## Формат .mdc

```markdown
---
description: Python style for this repo
globs: ["src/**/*.py", "tests/**/*.py"]
alwaysApply: false
enforce: .venv/bin/python -m ruff check --no-cache $RULE_FILES
---

Запрещены wildcard-импорты. Типы на всех публичных функциях.
```

Поля:
- `description`, `globs` (строка с запятыми или список), `alwaysApply` — как в Cursor.
- `enforce` — опциональное расширение: shell-команда. Запускается с cwd = корень
  правил; изменённые файлы передаются аргументами и в env `RULE_FILES`
  (newline-separated) и `RULE_ROOT`. Timeout 120 s.

Источники (в порядке приоритета): `.cursor/rules/*.mdc`, затем `.claude/rules/*.md`
(frontmatter опционален; без него правило считается `alwaysApply: true` — так
ведёт себя сам Claude Code). Файлы, сгенерированные `/context-rules sync`,
при чтении пропускаются.

## Claude Code-совместимость

```
/context-rules sync
```

Генерирует `.claude/rules/<name>.md` из каждого `.mdc`: frontmatter стрипается,
тело + шапка «применяй при работе с файлами <globs>». Claude Code грузит их
плоско (у него нет условности), Hermes — через этот плагин условно. Roundtrip
безопасен: generated-файлы помечены комментарием и при чтении пропускаются.

## Команды

- `/context-rules status` — найденные правила, типы, корни
- `/context-rules list` — имена
- `/context-rules show <name>` — полный текст правила
- `/context-rules sync` — генерация `.claude/rules/*.md`
- `/context-rules verify` — прогнать enforce-команды по изменённым файлам сейчас

## Установка

Каталог плагина кладётся/симлинкается в `$HERMES_HOME/plugins/context-rules/`
(или `~/.hermes/plugins/context-rules`), подхватывается при следующем старте
Hermes. Зависимости: только stdlib; PyYAML опционален (fallback-парсер
frontmatter встроен).

Использует только документированные поверхности плагинов:
`register_middleware("tool_execution")`, `register_hook("post_tool_call")`,
`register_hook("pre_verify")`, `register_system_prompt_section`,
`register_command`.

## Ограничения (MVP)

- Пути в `terminal` командах не парсятся — только `workdir` (шумно и ненадёжно).
- Относительные пути в tool-аргументах резолвятся от `os.getcwd()` процесса Hermes.
- Инъекция полного текста правила — один раз за сессию; при смене файла правил
  в живой сессии индекс промпта не обновляется (это цена prompt caching).
