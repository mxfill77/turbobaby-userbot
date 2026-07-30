# Разведка возможностей платформы под наш контур — 28.07.2026

Только чтение и документация. Ни один боевой файл не изменён; правки не предлагаются к
применению без решения владельца. Все числа — замеры по нашим логам/транскриптам, все условия —
дословные цитаты первоисточников.

Наш способ вызова (инвентарь, `file:line`):
> **⚠️ УСТАРЕЛО с 30.07.2026:** модель исполнителя здесь снята. Сегодня `EXECUTOR_MODEL =
> "claude-opus-5"` (искать по имени константы — номер строки `:610` тоже уехал).
> Разбор расхода/качества исполнителя по этому артефакту пойдёт по НЕ работающей голове.

- исполнитель: `pc_orchestrator.py:610` → `claude -p --model claude-opus-4-8 --effort xhigh <prompt>`,
  **текстовый режим** (без `--output-format json`), `cwd=REPO`;
- думатель: `pc_orchestrator.py:2012` → `claude -p ... --model $THINKER_MODEL --effort ...
  --output-format json --max-turns 1 --allowed-tools "" --fallback-model $THINKER_FALLBACK`,
  `cwd=tempfile.gettempdir()`;
- клиентский черновик: `suggest.py:4401` → `claude -p <user> --system-prompt <большой блок>
  --model $SUGGEST_MODEL --output-format json --allowed-tools "" --fallback-model $SUGGEST_MODEL_FALLBACK`,
  `cwd=temp`;
- бронь/модерация: `booking_draft.py:79`, `moderation_core.py:30` — тем же путём через suggest;
- прямой API (`anthropic.messages.create`) есть только в выключенной ветке `suggest.py:4182` и
  в `collect_booking.py:129`; боевой путь — CLI по подписке Max.

---

## 1) Кеширование промптов

### Что кешируется и как включается у НАС

Для API кеш надо включать руками. Дословно
([prompt-caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md)):

> "There are two ways to enable prompt caching: **Automatic caching**: Add a single `cache_control`
> field at the top level of your request. … **Explicit cache breakpoints**: Place `cache_control`
> directly on individual content blocks…"

Но мы зовём не API, а `claude`, и там ручек не нужно
([How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching)):

> "Claude Code handles prompt caching for you, unless you [disable it]."

Порядок слоёв (что во что попадает) — оттуда же:

| Layer | Content | Changes when |
|---|---|---|
| System prompt | Core instructions, tool definitions, output style | The set of loaded tool definitions changes, or Claude Code is upgraded |
| Project context | CLAUDE.md, auto memory, unscoped rules | Session starts, or after `/clear` or `/compact` |
| Conversation | Your messages, Claude's responses, tool results | Every turn |

### Ограничения, которые нас реально касаются

1. **TTL на подписке — час, а не 5 минут.** Дословно:
   > "On a Claude subscription, Claude Code requests the one-hour TTL automatically. Usage is
   > included in your plan rather than billed per token, so the longer TTL costs you nothing extra…"
   > "If you've gone over your plan's usage limit and Claude Code is drawing on usage credits, you
   > are billed for that usage, so Claude Code automatically drops the TTL to five minutes."

2. **Кеш привязан к машине И каталогу, а у последовательных сессий — ещё и к git-снимку.** Дословно:
   > "In Claude Code, the cache is effectively scoped to one machine and directory. The system prompt
   > embeds the working directory, platform, shell, OS version, and auto-memory paths…"
   > "Sequential sessions share the prefix only when the git status snapshot at startup matches,
   > since the system prompt also captures branch and recent commits."

   **Это прямо про наш исполнитель.** Он ходит `cwd=REPO` и по итогам задачи **коммитит**. Значит
   следующий `claude -p` стартует с изменившимся git-снимком → системный слой не совпал → холодный
   старт. Работа орка (коммит за коммитом) сама себе рушит префикс.

3. **Минимальная длина кешируемого префикса** (живая страница; в оффлайн-таблице скилла числа
   устаревшие — 2048/4096, верим живому доку):
   > "512 tokens for Claude Opus 5, Claude Fable 5, and Claude Mythos 5 … 1,024 tokens for Claude
   > Opus 4.8, Claude Sonnet 5, Claude Sonnet 4.6 … 4,096 tokens for Claude Haiku 4.5"

   Наш system-промпт `suggest.make_system_prompt()` (`suggest.py:3962-4169`: CRITICAL_FACTS + 6 правил
   + STYLE_GUIDE + few-shot + плейбук до 100 правил + FAQ) на порядок больше любого из порогов —
   **по размеру подпадают 100% наших вызовов**.

4. **Сабагенты кешируются по 5 минут даже на подписке:**
   > "Subagents use the five-minute TTL even on a subscription, since the automatic one-hour TTL
   > applies to the main conversation."

5. Цена (для справки, к подписке не применяется): "5-minute cache write tokens are 1.25 times the
   base input tokens price … 1-hour cache write tokens are 2 times … Cache read tokens are 0.1 times".

### Сколько наших вызовов подпадает — ЗАМЕР, не оценка

Транскрипты `bridge-transcript-cse_*.jsonl` (6 файлов, 405 записей `message.usage`), снимок 28.07 15:15:

| файл | usage-записей | input | cache_read | cache_creation | доля чтения из кэша |
|---|---|---|---|---|---|
| 014KwWnSLVYb… | 80 | 141 | 6 129 522 | 518 624 | 92.20 % |
| 015JSKXNMCRx… | 11 | 11 152 | 334 345 | 110 029 | 73.40 % |
| 016WXCF8f1VK… | 89 | 354 | 5 781 505 | 2 013 153 | 74.17 % |
| 019L2esQnD63… | 9 | 18 | 276 200 | 123 527 | 69.09 % |
| 01AyUG2oPb7p… | 67 | 18 144 | 5 268 900 | 370 840 | 93.12 % |
| 01VfzYw5adZm… | 149 | 277 | 28 221 502 | 1 220 786 | 95.85 % |
| **ИТОГО** | **405** | **30 086** | **46 011 974** | **4 356 959** | **91.30 %** |

- свежий (неоплаченный кэшем) вход — **0.06 %** промпт-токенов;
- ходов с `cache_read_input_tokens == 0` — **6 из 399 (1.5 %)**, т.е. **98.5 % ходов бьют в кэш**;
- отношение чтение:запись = **10.6 : 1**;
- TTL-разрез: **4 005 347 токенов записано с 1h TTL (92.5 %)** против **325 450 с 5m (7.5 %)** —
  ровно как в доке: основная нить час, сабагенты пять минут.

**Вывод по замеру: на RC/Code-полосе кеш уже включён и почти насыщен — запаса там нет.**

Где мы кеш НЕ видим (и потому не управляем):
- исполнитель: `pc_orchestrator.log` — `METRICS … tokens_in=na tokens_out=na` в **4/4** строках
  (текстовый режим, `extract_tokens` получает не-dict → `(None, None)`);
- клиентский черновик: `suggest.py` кэш-поля **уже парсит** (`_MU_INPUT_FIELDS` = `inputTokens`,
  `cacheReadInputTokens`, `cacheCreationInputTokens`, `suggest.py:4335`), использует сумму только как
  ключ argmax для определения головы (`suggest.py:4366`) и **выбрасывает число**: в лог уходит
  `SUGGEST: голова=%s (просили %s, фолбэк %s)` (`suggest.py:4419`) — 178 строк в `userbot.log`,
  40 в `moderation_bot.log`, **ни одного числа**;
- `rc_server_debug.log`: ключ `cache_read_input_tokens` встречается 248 раз, но **все 248 значений —
  литерал `[REDACTED]`**.

Разрывы между соседними вызовами suggest (к вопросу о TTL): 218 вызовов, 216 промежутков — **110 < 5 мин,
106 ≥ 5 мин**; медиана в `userbot.log` — **461 с**. Внутри часа — **123 из 177 (69 %)**. То есть 5-минутный
TTL промахнулся бы больше чем в половине случаев, а часовой (наш, по подписке) забирает две трети.

### Вердикт по п.1

**Применимо, но включать нечего — уже работает.** Что даст: ничего нового само по себе; ценность в
двух местах. (а) Исполнитель гонит Opus 4.8 на `xhigh` и мы **не знаем ни одного токена** — `--output-format
json` закрыл бы дыру, `extract_tokens` уже готов и покрыт тестом (`test_pc_orchestrator.py:5809`).
(б) Кеш исполнителя, вероятно, холодный после каждого коммита — это стоит замерить, а не додумывать.
Чего будет стоить: две правки на одну строку каждая (флаг у исполнителя, `%d` в логе suggest) и
осторожность с `--bare` — см. предупреждение в п.2.

---

## 2) Хранилище ключей

Кандидатов два, и это РАЗНЫЕ вещи.

### 2а. Vaults платформы (Managed Agents) — для секретов третьих сервисов

[Authenticate with vaults](https://platform.claude.com/docs/en/managed-agents/vaults.md). Как класть:
`POST /v1/vaults` → `POST /v1/vaults/{id}/credentials` (`ant beta:vaults:credentials create`), тип
`environment_variable` с `secret_name`/`secret_value`. Как читать — **никак**:

> "The actual credential values you supply (`token`, `access_token`, `refresh_token`,
> `client_secret`, `secret_value`) are treated as sensitive, write-only fields and never returned in
> API responses."

Как секрет доходит до кода:

> "each credential is keyed by a `secret_name` (the environment variable name) and stored in the
> sandbox as an opaque placeholder. When the agent initiates an outbound request, the opaque
> placeholder is substituted with the real secret at egress. The agent never sees the secret value."

Ограничения, которые закрывают вопрос:
> "**Maximum 20 credentials per vault.**" / "**Keys are immutable.**" / "Vaults and credentials are
> **workspace-scoped**, meaning anyone with an API key for the same workspace can reference them…"
> "Environment variable credentials (`environment_variable`) are not yet supported with self-hosted
> sandboxes."
> "The substitution happens at egress, not inside the sandbox. Anything that processes the credential
> locally sees the opaque placeholder, not the real value: clients that validate the credential format
> at startup may reject it, and clients that compute a request signature from the secret (for example,
> AWS SigV4) produce an invalid signature."

Подстановка работает **только** внутри сессии Managed Agents (`vault_ids` передаётся в
`POST /v1/sessions`) и только на исходящем трафике песочницы Anthropic. Наш `BRIDGE_TOKEN` читает
локальный питон на ПК и на VPS (`brain_writer.py:84`, `cowork_log_append.py:48`,
`pc_orchestrator.py:182`, `delivery.py:44`) — никакой песочницы Anthropic в этом пути нет.

### 2б. Хранилище учёток самого Claude Code — для аутентификации в Anthropic

[Authentication → Credential management](https://code.claude.com/docs/en/iam):

> "On macOS, credentials are stored in the encrypted macOS Keychain. On Linux, credentials are stored
> in `~/.claude/.credentials.json` with file mode `0600`. On Windows, credentials are stored in
> `%USERPROFILE%\.claude\.credentials.json` and inherit the access controls of your user profile
> directory, which restricts the file to your user account by default."

Безголовый режим — есть два законных пути:
> "For CI pipelines, scripts, or other environments where interactive browser login isn't available,
> generate a one-year OAuth token with `claude setup-token`… copy it and set it as the
> `CLAUDE_CODE_OAUTH_TOKEN` environment variable"
> "**Custom credential scripts**: the `apiKeyHelper` setting can be configured to run a shell script
> that returns an API key." / "by default, `apiKeyHelper` is called after 5 minutes or on HTTP 401
> response. Set `CLAUDE_CODE_API_KEY_HELPER_TTL_MS`…"

Два предупреждения под наш контур:
- **`--bare` убьёт нашу авторизацию по подписке.** Дословно ([headless](https://code.claude.com/docs/en/headless)):
  > "Bare mode skips OAuth and keychain reads. Anthropic authentication must come from
  > `ANTHROPIC_API_KEY` or an `apiKeyHelper` in the JSON passed to `--settings`."
  и в iam: "Bare mode does not read `CLAUDE_CODE_OAUTH_TOKEN`."
  Наш демон живёт именно на `.credentials.json` (см. память `pc-orchestrator-headless-auth`) — флаг
  «для ускорения старта» стоил бы нам всей полосы.
- порядок старшинства учёток: `ANTHROPIC_API_KEY` **бьёт** подписку («If you have an active Claude
  subscription but also have `ANTHROPIC_API_KEY` set in your environment, the API key takes
  precedence once approved») — ровно наш класс #«credit too low»; поэтому наши `env.pop("ANTHROPIC_API_KEY")`
  перед каждым спавном (`pc_orchestrator.py:780`, `suggest.py:4399`, `booking_draft.py:77`) —
  не паранойя, а необходимость. Ничего менять не надо.

### Вердикт по п.2

**Vaults — НЕ применимо. Наш файл с токеном они не заменяют.** Vault отдаёт секрет только исходящему
запросу из песочницы Managed Agents и никогда не отдаёт значение обратно; наш токен нужен локальному
питон-процессу на ПК/VPS. Что дало бы: ничего, при этом добавило бы зависимость от Managed Agents,
workspace-scoped доступ («anyone with an API key for the same workspace») и API-биллинг вместо подписки.
Хранилище учёток Claude Code — **применимо и уже используется** (`%USERPROFILE%\.claude\.credentials.json`),
на безголовом работает; полезный запас — `claude setup-token` + `CLAUDE_CODE_OAUTH_TOKEN` как
переносимая замена интерактивному логину (годовой срок), если учётка снова протухнет.

---

## 3) Advisor

### Как устроен

[Advisor tool (API)](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool):
> "The advisor tool lets a faster, lower-cost **executor model** consult a higher-intelligence
> **advisor model** mid-generation for strategic guidance. The advisor reads the full conversation,
> produces a plan or course correction, and the executor continues with the task."

Главное для нас: **это есть прямо в Claude Code**, переписывать на API не нужно
([Escalate hard decisions with the advisor tool](https://code.claude.com/docs/en/advisor)):
> "You can set the advisor model in three ways: **`/advisor` command** … **`advisorModel` setting** …
> **`--advisor` flag**: set the advisor for a single session at launch"
> "The advisor runs server-side on Anthropic's infrastructure as a server tool, **available to both
> subscription and API-billed accounts**. You choose which model acts as the advisor, and Claude
> decides when to call it."

### Чем отличается от нашей лестницы

Наша «лестница» — это **фолбэк по отказу**: `--fallback-model` у думателя (`pc_orchestrator.py:2014`,
fable-5 → opus-4-8) и у suggest (`suggest.py:4401`, fable → sonnet). Срабатывает, когда первая голова
не смогла, и заменяет её целиком. Advisor — не замена, а **консультация внутри одного вызова**: слабая
голова генерирует, сильная планирует. Разница по таблице из доки Claude Code:

| Approach | When the stronger model runs | How it starts |
|---|---|---|
| Advisor tool | At decision points mid-task | Claude calls it when it needs guidance |
| `opusplan` | During plan mode, then switches to Sonnet for execution | You enter plan mode |
| Subagents with `model` set | For the entire delegated subtask | Claude delegates |
| `/model` | For all subsequent turns | You switch models |

Кто чем может советовать (наши модели выделены):

| Main model | Accepted advisors | Notes |
|---|---|---|
| Haiku 4.5 | Fable, Opus, Sonnet | "Haiku can call the advisor but cannot act as one" |
| Sonnet 5 | Fable, Opus, Sonnet 5 | "A Sonnet 4.6 advisor is rejected" |
| **Opus 4.7 or later** | **Fable, and Opus 4.7 or later** | "An Opus 4.7 main with an Opus 4.6 or Sonnet 5 advisor is rejected" |
| **Fable 5** (v2.1.170+) | **Fable** | "An Opus or Sonnet advisor is rejected. **Fable isn't offered as the advisor, so a Fable 5 main model runs without one**" |

И отдельная блокировка:
> "Claude Code doesn't offer Fable 5 as the advisor. … the `/advisor` picker lists it as a dimmed,
> unselectable row labeled `Fable 5 (temporarily unavailable)`, and Claude Code rejects
> `/advisor fable` and `--advisor fable`."

Цена и кеш:
> "On subscription plans, advisor usage counts toward your plan's usage limits."
> "**The advisor model's own read of the conversation is not cached. Each advisor call processes the
> full transcript anew, with no reuse between calls.**"
> "Enabling or disabling the advisor mid-session does not invalidate your main model's prompt cache."

Из API-доки — сколько это в токенах и почему бывает дешевле:
> "Advisor output is typically 400 to 700 text tokens, or 1,400 to 1,800 tokens total including
> thinking. The cost savings come from the advisor not generating your full final output."
> "For coding tasks, pairing a Sonnet executor at medium effort with an Opus advisor achieves
> intelligence comparable to Sonnet at default effort, at lower cost."
> "Without system-prompt steering, the executor tends to under-call the advisor in some domains,
> **particularly coding tasks**."

Ограничения: "The advisor tool is **experimental** and requires the Anthropic API. It is not available
on Amazon Bedrock, Claude Platform on AWS, Google Cloud's Agent Platform, or Microsoft Foundry.
Behavior, pricing, and availability may change." Требуемая версия для Fable-веток — v2.1.170+.
Выключатель — `CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1`. Каппа на число вызовов в Claude Code нет:
"There is no setting to cap or force advisor calls."

### Вердикт по п.3

**Нашу лестницу НЕ заменяет — это другой инструмент** (фолбэк по отказу против консультации по ходу).
Применимо **частично и только на одной полосе**: у думателя `THINKER_MODEL=claude-fable-5` — Fable как
главная модель советчика не получает вообще («runs without one»), у исполнителя `claude-opus-4-8`
советчиком может быть только другой Opus 4.7+ («A second Opus reviews the first. Useful for high-stakes
tasks where an independent check matters more than cost»). Что даст: независимую проверку плана на
дорогих задачах исполнителя. Чего будет стоить: советчик читает **весь транскрипт заново на каждый
вызов и без кеша**, на подписке это прямо в лимиты плана; ограничить число вызовов из Claude Code
нечем; статус — experimental. Плюс наш собственный фолбэк придётся оставить как есть: advisor его не
покрывает.

---

## 4) Аналитика расходов

### Видно ли наши вызовы уже сейчас

Карта из [Manage costs effectively](https://code.claude.com/docs/en/costs) — где что видно:

| Setup | See spend | Per-user reporting |
|---|---|---|
| Claude for Teams or Enterprise | Spend report in org analytics | Spend report CSV; Enterprise Analytics API |
| Claude Console (API) | Console usage page | Console dashboard, Claude Code Analytics API |
| Bedrock / Google / Foundry | Your cloud billing console | OpenTelemetry или LLM gateway |

Мы — индивидуальная подписка Max по OAuth, без API-ключа. Значит **ни консольная страница Usage
(это про API-организации), ни org-дашборд `claude.ai/analytics/claude-code` (это про Teams/Enterprise)
наших вызовов не показывают.** Что реально есть:

> "The Session block in `/usage` shows API token usage and is intended for API users. **Claude Max and
> Pro subscribers have usage included in their subscription, so the session cost figure isn't relevant
> for billing purposes.** Subscribers see plan usage bars, activity stats, and a usage breakdown on the
> same screen."
> "The figures are approximate and **computed from local session history on this machine**, so usage
> from other devices or claude.ai is not included."
> "Claude Code computes the dollar figure locally from token counts priced at standard list rates, so
> it doesn't reflect promotional pricing or contracted discounts and may differ from your actual bill."

`/usage` — это команда интерактивного терминала; в `-p` встроенные терминальные команды недоступны
(«Built-in commands that only run in the terminal interface, such as `/login`, aren't available in `-p`
mode»), так что для нашей автоматики она не канал.

### Что бы дало числа по нашим вызовам

Два рабочих пути, оба совместимы с подпиской.

**(а) Собственный JSON на каждом вызове** ([headless](https://code.claude.com/docs/en/headless)):
> "With `--output-format json`, the response payload includes `total_cost_usd` and a per-model cost
> breakdown, so scripted callers can track spend per invocation without consulting the usage dashboard."

Поля разложены в [Track cost and usage](https://code.claude.com/docs/en/agent-sdk/cost-tracking):
`usage` (без сабагентов), `total_cost_usd` (с сабагентами), `modelUsage` (с сабагентами, по моделям:
`inputTokens`, `outputTokens`, `cacheReadInputTokens`, `cacheCreationInputTokens`, `costUSD`). Там же
честная оговорка:
> "The `total_cost_usd` and `costUSD` fields are **client-side estimates, not authoritative billing
> data**. … Do not bill end users or trigger financial decisions from these fields."
> "The three result-level fields differ in what they count when the agent spawns subagents. Use
> `modelUsage` … for whole-tree token accounting; **the `usage` field undercounts as soon as nesting
> occurs**."

Это ровно то, что `task_metrics.extract_tokens` уже умеет читать (`task_metrics.py:146-152`,
оба нейминга — `usage.*` и `modelUsage[*].*`), но в бою не получает: исполнитель зовётся без флага.

**(б) OpenTelemetry** ([Monitor usage](https://code.claude.com/docs/en/monitoring-usage)) —
`CLAUDE_CODE_ENABLE_TELEMETRY=1` + `OTEL_METRICS_EXPORTER`/`OTEL_EXPORTER_OTLP_ENDPOINT`. Метрики:
`claude_code.token.usage` (атрибут `type`: `input`, `output`, **`cacheRead`, `cacheCreation`**; плюс
`model`, `query_source` = `main`/`subagent`/`auxiliary`, `effort`, `agent.name`, `skill.name`),
`claude_code.cost.usage` (USD), `claude_code.session.count`, `claude_code.active_time.total`, и события
`claude_code.api_request` / `api_error` / `api_refusal`. Из доки costs: "OpenTelemetry export works on
every setup and is the only option that streams per-user token and cost metrics into your own
observability stack in near real time." Оговорка честная: страница **не** утверждает прямо, что
телеметрия работает в `-p` и на подписке — только косвенно («In Agent SDK and non-interactive sessions
started with `-p`, Claude Code also reads `TRACEPARENT` and `TRACESTATE`»). Это надо проверять замером,
а не объявлять.

### Вердикт по п.4

**Готовой платформенной аналитики под нас НЕТ** — консоль и org-дашборд не про индивидуальную подписку,
`/usage` локален, приблизителен и недоступен в `-p`. **Применимо своими руками:** `--output-format json`
даёт `modelUsage` с кэш-полями на каждый вызов, и наш `extract_tokens` под это уже написан и
протестирован. Что даст: строки METRICS с настоящими числами вместо `tokens_in=na` (сейчас 4/4 строки
пустые) и видимость доли кэша на клиентской полосе (сейчас 0 чисел в 218 строках лога). Чего будет
стоить: доллары в этих полях — клиентская оценка по прайс-листу, для подписки смысла не имеют; считать
надо ТОКЕНЫ (они и есть лимит плана). OTel — отдельный контур сбора, заводить только если нужен
временной ряд, и сначала замером подтвердить, что он вообще пишет из `-p`.

---

## Остатки и точки касания (не сделано, требует решения)

1. Исполнитель без `--output-format json` — `pc_orchestrator.py:610`; приёмник готов
   (`task_metrics.extract_tokens`, `pc_orchestrator.py:861-869`). Правка класса «одна строка».
2. Кэш-числа suggest парсятся и выбрасываются — `suggest.py:4366` считает, `suggest.py:4419` не пишет.
3. Гипотеза (НЕ проверена): коммит исполнителя рушит git-снимок в системном промпте и делает
   следующий `-p` холодным. Проверяется только после п.1.
4. Класс-фикс на обе полосы: то же `na` в METRICS почти наверняка и на VPS-полосе — проверить
   `EXECUTOR_EFFORT`/вызов там же, иначе зеркальная дыра (правило 9 свода).
5. `--bare` не добавлять никуда: убивает чтение keychain/OAuth, а значит и подписку.
