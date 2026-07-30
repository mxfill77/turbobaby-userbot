# Можно ли включить режим оркестрации помощников по умолчанию — 28.07.2026

Только чтение и документация. Ни один боевой файл не изменён. Числа — замеры по логам этой машины,
условия — дословные цитаты первоисточников.

Версия CLI на этой машине (из боевого лога, `rc_server_debug.log`): `cc_version=2.1.218.3df`.
Это важно: часть ручек ниже требует 2.1.203+ (есть) или 2.1.219+ (**нет**).

---

## 1) Все настройки уровня усилий и режима — по слоям

### Что вообще существует (дословно из «Available settings»)

| Ключ | Дословное описание |
|---|---|
| `effortLevel` | "Persist the effort level across sessions. **Accepts `"low"`, `"medium"`, `"high"`, or `"xhigh"`.** Written automatically when you run `/effort` with one of those values. `--effort` and `CLAUDE_CODE_EFFORT_LEVEL` override this for one session." |
| `alwaysThinkingEnabled` | "Enable extended thinking by default for all sessions… To force thinking off regardless of this setting, set `MAX_THINKING_TOKENS=0` in `env`… except on Fable 5, which cannot have thinking turned off" |
| `disableWorkflows` | "**Default**: `false`. Disable dynamic workflows and the bundled workflow commands. Equivalent to setting `CLAUDE_CODE_DISABLE_WORKFLOWS` to `1`" |
| `workflowSizeGuideline` | размер фан-аута; **«Requires Claude Code v2.1.219 or later»** — у нас 2.1.218, ключ недоступен |
| `teammateMode` | режим отображения помощников команды (`in-process`/`auto`/`tmux`/`iterm2`) |
| `advisorModel` | "Model for the server-side advisor tool… **Unset to disable the advisor.**" |
| `fallbackModel` | "Fallback model(s) to try in order when the primary model is overloaded or unavailable… Chains are capped at three models" |
| `availableModels` / `enforceAvailableModels` | ограничение выбора моделей |
| `agent` | "Run the main thread as a named subagent… Applies that subagent's system prompt, tool restrictions, and model" |
| `disableAutoMode` / `autoMode` | управление auto-режимом разрешений |
| `outputStyle` | часть системного промпта, читается один раз на старте |

Переменные среды того же назначения: `CLAUDE_CODE_EFFORT_LEVEL`, `MAX_THINKING_TOKENS`,
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`, `CLAUDE_CODE_DISABLE_WORKFLOWS`, `CLAUDE_CODE_SUBAGENT_MODEL`,
`CLAUDE_CODE_DISABLE_ADVISOR_TOOL`. Флаги: `--effort`, `--model`, `--advisor`, `--teammate-mode`,
`--fallback-model`.

### Старшинство слоёв (дословно)

> "1. **Managed** (highest): can't be overridden by anything. 2. **Command line arguments**: temporary
> session overrides. 3. **Local**: overrides project and user settings. 4. **Project**: overrides user
> settings. 5. **User** (lowest)…"
> "On Windows, paths shown as `~/.claude` resolve to `%USERPROFILE%\.claude`."

### Что уже задано у НАС

**Слой managed — ОТСУТСТВУЕТ.** Рекурсивный поиск `managed-settings.json` под `C:\ProgramData` — ноль
попаданий; каталогов `C:\ProgramData\ClaudeCode` и `C:\ProgramData\Claude Code` нет. Сверху ничего не
навязывается.

**Слой project — `D:\turbobaby-bot\.claude\settings.json` (единственное место, где заданы ручки мощности):**
```
2    "model": "claude-opus-5",
3    "effortLevel": "xhigh",
4    "alwaysThinkingEnabled": true,
5    "env": {
6      "MAX_THINKING_TOKENS": "31999"
7    },
9    "permissions": { "defaultMode": "acceptEdits",
```
Плюс 4 хука (см. п.3). `MAX_THINKING_TOKENS=31999` подтверждён живым в окружении текущего процесса.

**Слой local — `.claude\settings.local.json`:** только `permissions.allow` (63 записи). Ручек мощности нет.

**Слой user — `C:\Users\mxfill1\.claude\settings.json`:** ручек мощности нет; из режимного есть
только `"skipWorkflowUsageWarning": true` (это глушитель предупреждения, не режим), `theme`,
две нотификационные ручки.

**Слой env:** в `.env` нет ни одного `CLAUDE_CODE_*`; в User- и Machine-области Windows — ноль
переменных по маскам CLAUDE/EFFORT/TEAM/ULTRA/WORKFLOW. Никакой код в репозитории не присваивает
`CLAUDE_CODE_*`.

**Слой флагов (кто что передаёт при запуске `claude`):**
| Полоса | file:line | Модель | Усилие |
|---|---|---|---|
| исполнитель | `pc_orchestrator.py:610` | `--model claude-opus-4-8` (константа `:590`) ⚠️ **устарело с 30.07: сегодня `EXECUTOR_MODEL = "claude-opus-5"`** | `--effort xhigh` (из settings.json через `:609`) |
| думатель | `pc_orchestrator.py:2012` | `--model $THINKER_MODEL` | `--effort` из settings.json |
| suggest | `suggest.py:4401` | `--model $SUGGEST_MODEL` | **`--effort` не передаётся** → дефолт CLI |
| бронь | `booking_draft.py:80` | `suggest.SUGGEST_MODEL` | **`--effort` не передаётся** |
| RC-канал | `rc_supervisor.py:283` | ничего не передаёт → берёт project settings | то же |

**Итог по п.1:** мощность задана ровно в одном файле — project `settings.json`; `max` в
`effortLevel` документацией **не предусмотрен** (только low/medium/high/xhigh), так что «xhigh» —
это уже потолок персистентной настройки. Две полосы (suggest, бронь) вообще идут на дефолтном
усилии CLI.

**Дрейф, требующий решения владельца:** `CLAUDE.md:10-12` и `.gitignore:3` до сих пор описывают
дефолт репозитория как `claude-fable-5`, а живой `settings.json:2` — `claude-opus-5`. Рядом с живым
файлом лежат устаревшие копии `settings.json.new` и `settings.json.bak-2026-07-23` (обе с fable-5) —
восстановить по ошибке легко.

---

## 2) Есть ли параметр, включающий режим при старте — или только командой

Речь о двух РАЗНЫХ режимах. Ответ по каждому — дословно.

### 2а. Ultracode (автоматическая оркестрация воркфлоу) — флаг при старте ЕСТЬ, персистентной настройки НЕТ

> "**Ultracode is a Claude Code setting that combines `xhigh` reasoning effort with automatic workflow
> orchestration.** With it on, Claude plans a workflow for each substantive task instead of waiting for
> you to ask."
> "```/effort ultracode```"
> "**To start a session with ultracode already on, launch with `claude --effort ultracode`. Requires
> Claude Code v2.1.203 or later.**"
> "**Ultracode lasts for the current session and resets when you start a new one.** Drop back with
> `/effort high` when you return to routine work."

То есть: **параметр запуска есть** (`--effort ultracode`, наша 2.1.218 подходит), но «по умолчанию»
включить нельзя — `effortLevel` в settings.json принимает только `low|medium|high|xhigh`, а ultracode
«resets when you start a new one».

Отдельно — ключевое слово в промпте, и оно у нашей автоматики не работает:
> "The keyword is an opt-in only in a prompt you type yourself: at the interactive prompt, in an IDE
> extension panel, **in a Remote Control client**, or in an Agent SDK application that stamps your
> keyboard input's `origin` as `{ kind: "human" }`. It doesn't start a workflow when it reaches the
> session another way: **a prompt passed with `-p`**, … **a scheduled task prompt**, a webhook payload…"

Значит: с телефона по RC слово `ultracode` работает; из `pc_orchestrator`/`suggest` (всё через `-p`) и
из задачи Планировщика — **инертно**.

Что при этом снимается с предохранителей (важно для `-p`):
> "| Bypass permissions, `claude -p`, Agent SDK | **Never [prompted]. The run starts immediately** |"
> "Sessions with ultracode on don't show the [Large workflow] warning, because turning ultracode on
> already opts you in to large runs."
> "Up to 16 concurrent agents… 1,000 agents total per run"
> "The default is `medium`. … Requires Claude Code v2.1.219 or later; **earlier versions default to
> `unrestricted`**" — на нашей 2.1.218 ограничителя размера нет по умолчанию.
> "Runs count toward your plan's usage and rate limits like any other session."

И наша собственная защёлка: `task_metrics.py:113-114` — `norm_effort()` возвращает `DEFAULT_EFFORT`
для любого значения вне `low|medium|high|xhigh|max`. То есть если вписать `effortLevel: "ultracode"`
в settings.json, **наш же код молча подменит его на `xhigh`** для полос исполнителя и думателя.

### 2б. Agent teams (команда помощников) — настройка ЕСТЬ, персистентная

> "**Agent teams are experimental and disabled by default. Enable them by setting
> `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in your settings.json or environment. Without that variable,
> no team is set up at session start, no team directories are written, and Claude does not spawn or
> propose teammates.**"
> ```json
> { "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
> ```
> "In both cases, you stay in control. **Claude won't spawn teammates without your approval.**"
> "Agent teams use significantly more tokens than a single session."

Это единственный из двух режимов, который **действительно включается по умолчанию** через `env` в
settings.json. Слэш-команды для включения нет. Ограничения (дословно, выборка): "No session resumption
with in-process teammates", "One team per session", "No nested teams", "Lead is fixed",
"Permissions set at spawn", "Split panes require tmux or iTerm2" (на Windows Terminal не поддержано).

---

## 3) Хук на старт сессии

**У нас `SessionStart` НЕ настроен.** Все хуки живут только в project `settings.json:83-125`:

| Событие | Matcher | Команда |
|---|---|---|
| `Notification` | — | `dispatch_notify.py --hook notification` |
| `Stop` | — | `dispatch_notify.py --hook stop` |
| `SessionEnd` | — | `dispatch_notify.py --hook session_end` |
| `PreToolUse` | `Bash\|PowerShell\|Edit\|Write\|Read\|MultiEdit\|NotebookEdit` | `pretool_guard.py` |

`SessionStart` — **not configured**. `Setup` — **not configured**.

**Проверка фактом, работает ли механизм хуков.** Работает, и это видно по числам:
- `dispatch_notify.log`: `хук=stop` — **164** записи, `хук=session_end` — **58**, `хук=notification` — **1**;
  каждая строка печатает `MAX_THINKING_TOKENS=31999`, т.е. блок `env` из settings.json доходит до
  дочернего процесса хука;
- `pretool_guard.log` за трое суток: Bash **270**, Read **201**, Write **125**, Edit **69**,
  PowerShell **61** перехватов с вердиктами (`defer`/`ask`) — PreToolUse срабатывает на каждый вызов.
  Формат строки: `2026-07-28 15:01:40 | interactive | Bash | defer | - | ls -la | head -60 …`

**Но выставить режим этим хуком нельзя.** Дословно про SessionStart:
> "Runs when Claude Code starts a new session or resumes an existing session. Useful for loading
> development context like existing issues or recent changes to your codebase, or setting up
> environment variables."
> "**Any text your hook script prints to stdout is added as context for Claude.**"
> "SessionStart, Setup, and SubagentStart… **No blocking or decision control.**"

Возвращаемые поля — контекстные (`additionalContext` и несколько метаданных вроде `sessionTitle`);
поля «поставить усилие/модель/режим» в наборе нет. Плюс три независимых причины, почему это тупик:
1. `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` читается **при старте сессии** («no team is set up at session
   start») — хук, который стартует уже после, опаздывает по построению;
2. `ultracode` через подсунутый хуком текст не пройдёт: ключевое слово — opt-in только для
   «a prompt you type yourself», а хуковый текст человеческим вводом не считается;
3. даже если бы хук переписал settings.json: "A few keys are read once at session start and apply on
   the next restart instead: `model`… `outputStyle`", а `ultracode` вообще не является допустимым
   значением `effortLevel`.

Законный способ выставить режим на старте — **флаг процесса** (`--effort ultracode`,
`--teammate-mode`) или **`env` в settings.json** (agent teams), а не хук.

---

## 4) Журналы за трое суток: сколько раз реально запускались параллельные помощники

Окно 2026-07-25 … 2026-07-28 (локальное, UTC+7). Источники: 6 транскриптов
`bridge-transcript-cse_*.jsonl`, `rc_server_debug*.log`, `pretool_guard.log`, боевые логи ботов.
Сверх задания сверено с родным хранилищем `C:\Users\mxfill1\.claude\projects\**\*.jsonl` (933 файла).

| день | запусков помощников | из них воркфлоу | параллельных (2+ за один ход) |
|---|---|---|---|
| 2026-07-25 | 2 | 2 | 0 |
| 2026-07-26 | **0 — НИ РАЗУ** | 0 | 0 |
| 2026-07-27 | **0 — НИ РАЗУ** (вообще ноль строк инструментов) | 0 | 0 |
| 2026-07-28 | 4 | 0 | **1** |

**Прямо: наша автоматика — ни разу.** В `pc_orchestrator.log`, `pc_orchestrator.log.1`, `pc_agent.log`,
`dispatch_notify.log`, `userbot.log`, `moderation_bot.log` — **ноль** попаданий по всем девяти маркерам
(`Task(`, `subagent`, `teammate`, `agent_teams`, `parent_tool_use_id`, `Explore`, `general-purpose` и др.).
Все 6 запусков — из интерактивных/RC-сессий, и 4 из них — мои сегодняшние в этой самой разведке.

Две из трёх полос `claude -p` **структурно неспособны** позвать помощника:
`pc_orchestrator.py:2013` и `suggest.py:4402` передают `--allowed-tools ""` (+ `--max-turns 1` у
думателя). Единственная способная — исполнитель (`pc_orchestrator.py:610`, без ограничения
инструментов) — за трое суток не позвал ни одного.

**Agent teams не использовались ни разу.** Все попадания по `agent_teams`/`teammate` — это либо эхо
моего собственного промпта, либо WebFetch страницы документации:
```
2026-07-28T09:05:48.269Z [DEBUG] [bridge:activity] … tool_start Fetching https://code.claude.com/docs/en/agent-teams
```

**Поправка к промежуточному замеру (числа против сводки).** Субагент-счётчик вывел «максимум 1
помощник за запуск, параллельных — ноль», сгруппировав по ходу в транскрипте. Отладочный лог это
опровергает: два спавна в одном ходу, 106 мс друг от друга —
```
5660:2026-07-28T09:05:48.142Z … tool_use name=Agent description="Inventory effort/mode config layers" subagent_type="Explore"
5682:2026-07-28T09:05:48.248Z … tool_use name=Agent description="Count parallel helper launches 3 days" subagent_type="Explore"
```
Итого за окно: **один настоящий параллельный запуск на 2 помощника — мой, в этом самом запросе.**
Исторический максимум по всей машине до сегодня — 1 помощник за раз. Верим числам, не сводке.

Служебные строки, которые НЕ являются использованием (помечены как ложные срабатывания):
`CCR v2 subagent event reader registered for session resume` — присутствует в каждой сессии, означает
наличие возможности, а не факт вызова.

---

## 5) Нужен ли режим по умолчанию или хватает максимального усилия

**Прямо: режим по умолчанию не нужен. Максимального усилия достаточно.** Четыре причины, каждая с фактом.

1. **Половина ultracode у нас уже включена.** Ultracode — это «`xhigh` reasoning effort **combined with**
   automatic workflow orchestration». `xhigh` стоит в `settings.json:3` и явно передаётся флагом
   исполнителю и думателю. Новым был бы только автофан-аут.
2. **Включить его «по умолчанию» технически нельзя, а обходной путь опасен.** Персистентного значения
   нет («resets when you start a new one»), `effortLevel` его не принимает, а наш `norm_effort()`
   подменил бы такое значение на `xhigh` молча. Остаётся флаг `--effort ultracode` на полосе `-p`, где
   разрешения не спрашиваются («The run starts immediately»), предупреждение о большом прогоне
   подавлено, а ограничитель размера на 2.1.218 по умолчанию `unrestricted` — до 16 агентов
   одновременно и 1000 на прогон, всё в лимиты плана.
3. **Спроса нет — доказано журналами.** Единственная способная полоса (исполнитель) за трое суток не
   позвала ни одного помощника, хотя могла. Проблема не в отсутствующем режиме.
4. **Профиль наших задач — против.** Дословно: "For sequential tasks, same-file edits, or work with
   many dependencies, a single session or subagents are more effective." Наши задачи именно такие:
   один репозиторий, правка → тесты → гейт → коммит, с откатом при красном.

**Что делать вместо этого (без правок сейчас, на решение владельца):**
- держать ultracode как **ручной** инструмент там, где он и работает — слово `ultracode` в RC-промпте
  с телефона (RC прямо назван поддерживаемой поверхностью) или разовый `claude --effort ultracode`
  для заведомо тяжёлой разведки. Ровно так он и был применён сегодня;
- если когда-нибудь захочется включить — сначала поднять CLI до 2.1.219+, чтобы появился
  `workflowSizeGuideline` (иначе размер ничем не ограничен), и только потом обсуждать флаг;
- agent teams не включать: единственный по-настоящему персистентный режим
  (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` в `env`), но experimental, со сплит-панелями, не
  поддержанными в Windows Terminal, и без ни одного случая спроса за трое суток;
- закрыть дрейф документации (`CLAUDE.md` говорит fable-5, живой settings.json — `claude-opus-5`) и
  убрать/переименовать `settings.json.new` и `settings.json.bak-2026-07-23`, чтобы их нельзя было
  восстановить по ошибке;
- отдельно к прошлой разведке: полосы `suggest` и `booking_draft` идут вообще без `--effort` — если
  где-то и добавлять мощность осознанно, то там, а не в режиме оркестрации.
