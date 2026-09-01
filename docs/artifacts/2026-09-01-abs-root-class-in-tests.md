# Класс абсолютного корня в тестах — перепись обеих полос (01.09.2026)

**Разведка, правок ноль.** Предмет — класс «тест меряет живое дерево вместо своего»: тест
находит исходники по АБСОЛЮТНОМУ корню (`r"D:\turbobaby-bot"`, `"/root/turbobaby-manager-bot"`),
а не от собственного `__file__`. Итог одной строкой:

> **Корень класса лежит НЕ в тестах, а в ПРОДЕ.** Шесть продовых констант на двух полосах
> задают корень литералом; тесты его наследуют импортом и починкой тестов не лечатся.
> И обе названные в задании «образцовые» болячки живут на ОБЕИХ полосах, а не по одной на
> каждой: у ПК тоже хардкод в гарде, у сервера тоже три теста надзора за исходником демона.

---

## §1. Корень: хардкод в продовом модуле (измерено грепом обеих полос)

| полоса | файл:строка | константа | значение | как используется |
|---|---|---|---|---|
| ПК | `pc_orchestrator.py:98` | `REPO` | `r"D:\turbobaby-bot"` | `VENV_PY` (108), `REPO_SETTINGS` (4316), ~20 файлов состояния, **`cwd` всех `git`-вызовов** (`_git_out` 3951, `_git_run` 3964), **`client_contour.closure(REPO,…)` в `_dep_files()` (3989)**, `cwd` прогона юнитов (4069, 6055), `cwd` спавна демона (4086) |
| ПК | `pretool_guard.py:281` | `PROJECT` | `r"D:\turbobaby-bot"` | `VENV_PY` (283), `_GUARD_SOURCES`/`GUARD_LOG` (303–309), **`git -C PROJECT status`** (882), **`git -C PROJECT ls-files`** в `_is_repo_tracked` (2686), дефолт `cwd or PROJECT` при разборе команд (12 мест) |
| ПК | `pretool_guard.py:288` | `_MANAGER_CLONE` | `r"D:\foreign\turbobaby-manager-bot"` | **не дефект**: это СОЗНАТЕЛЬНО чужое дерево, а не самоссылка |
| VPS | `orchestrator_daemon.py:33` | `REPO` | `"/root/turbobaby-manager-bot"` | **строки 37–38: `load_dotenv(join(REPO,".env"))` + `sys.path.insert(0, REPO)`** |
| VPS | `orchestrator_daemon.py:34` | `BRIDGE_GS` | `"/root/turbobaby-bridge-gs"` | второе живое дерево, вне репозитория вовсе |
| VPS | `pretool_guard.py:78` | `PROJECT` | `"/root/turbobaby-manager-bot"` | разбор скриптов + `git -C PROJECT ls-files` |
| VPS | `guard_replay.py:36` | `PROJECT` | `"/root/turbobaby-manager-bot"` | дефолт `cwd=PROJECT` у `verdict()` |

**Самая дорогая строка переписи — `orchestrator_daemon.py:38`.** Простой `import orchestrator_daemon`
из копии дерева ПЕРЕВОДИТ `sys.path` и `dotenv` на живое дерево. То есть тест, который сам всё
делает правильно (корень от `__file__`), всё равно получает живой модуль. Это делает починку
«только тестов» на полосе VPS заведомо неполной.

Чисто (корень выведен от `__file__`) — и это готовый шаблон, он уже в обоих деревьях:
`gate.py:31` (VPS), `client_contour.py:82`, `session_watch.py:72`, `result_judge_pc.py:148`,
`expectations_pc_run.py:78`, `rc_supervisor.py:55`, `series_pc.py:110`, `lesson_router.py:30`,
`moderation_ipc.py`, `brain_writer.py`, `cowork_log_append.py`, `dispatch_notify.py`, `log_setup.py`,
`nonparse_scan.py`, `pc_agent.py`. Модули без корня вовсе (корень — параметр): `gate_selective.py`,
`selfupdate_gate.py`, `card_duty.py`, `task_metrics.py`, `bridge_http.py`, `price_gate.py`,
`queue_snapshot_pc.py`, `expectations_pc.py`.

---

## §2. Полоса ПК — перечень (файл : строка : что меряет)

### 2.1 Три теста надзора за зависимостями демона (образец из задания — ПОДТВЕРЖДЁН)

Ровно три метода строят путь к исходнику как `os.path.join(o.REPO, …)` и **читают/компилируют** его:

| # | файл:строка | метод | что делает |
|---|---|---|---|
| 1 | `test_pc_orchestrator.py:822` | `test_orch_runtime_covers_every_top_import_of_daemon` | `open(join(o.REPO,"pc_orchestrator.py"))` → `ast` верхних импортов; `isfile(join(o.REPO, mod+".py"))` (837); сверяет с `o._ORCH_RUNTIME` |
| 2 | `test_pc_orchestrator.py:10703` | `test_dirty_gate_watches_exactly_what_start_loads_unconditionally` | обходит транзитивное замыкание, `top_imports(join(o.REPO, name))` на каждом шаге; сверяет с `o._ORCH_RUNTIME` |
| 3 | `test_pc_orchestrator.py:10733` | `test_whole_closure_compiles_right_now` | `py_compile.compile(join(o.REPO, f))` по всем `o._dep_files()` |

### 2.2 Тот же класс через продовую функцию `_dep_files()` (косвенно, но так же слепо)

`_dep_files()` зовёт `client_contour.closure(REPO, …)` с модульным `REPO` — значит **любой** тест,
который его вызывает, считает замыкание по ЖИВОМУ дереву. В `TestSelfUpdateDepClosure` это:
`:10606`, `:10632`, `:10652`, `:10667`, `:10712`, `:10724`. Плюс `setUp:10518` делает
`os.listdir(o.REPO)` — то есть **весь класс из 11 методов засеян живым каталогом**, включая те,
что выглядят чисто-синтетическими (`FakeGit`). Ещё `:10647` — живой `git ls-tree` с `cwd=REPO`.

### 2.3 Живые конфиги и живой git

`test_pc_orchestrator.py:2387` (живой `.claude/settings.json` через `o.REPO_SETTINGS`),
`:10160` (живой `pc_orchestrator.gate_seen.json`), `:9352` (`selfupdate_gate.code_gate(..., o.REPO, …)` —
настоящие `py_compile`+import-smoke в живом дереве);
`test_card_terminal_log.py:436` (живой `.gitignore`);
`test_pretool_guard.py:39` — **свой литерал** `PROJ = r"D:\turbobaby-bot"`, от него: `:2219` и `:3772`/`:3802`
(читают живой `pretool_guard.py`, `pc_orchestrator.py`, `pc_agent.py`, `brain_writer.py`),
`:851`/`:1810`/`:4309` (живые `settings.json`/`settings.local.json`), `:1353` (живой `clasp_prod_pins.json`),
`:995` (живой `git ls-files`), `:664` (размер живого `pretool_guard.log`), `:4937` (живой корпус `docs/artifacts`).

### 2.4 Хуже, чем чтение: тест ПИШЕТ в живое дерево

| файл:строка | что пишет |
|---|---|
| `test_pc_orchestrator.py:304` | `open(join(o.REPO, f"pc_ask_{tid}.marker"), "w")` — маркер в корень живого репо (снимается в `finally`) |
| `test_pretool_guard.py:3861–3864` | `os.makedirs(join(PROJ,"tmp/deadprem-selftest"))` + запись `probe_body.py` в живое дерево; в той же команде (3869) зашит MSYS-путь `/d/turbobaby-bot/…` |

### 2.5 Хуже всего: тест перезапускает ЖИВОЙ прогон

`test_pretool_guard.py:372–376` — `subprocess.run([sys.executable,"-m","unittest",
"test_pretool_guard","test_pc_orchestrator"], cwd=PROJ, timeout=300)` и **`assertEqual(r.returncode, 0)`**.
Это не «читает живое дерево», это **делает вердикт копии функцией зелени живого дерева**.
Плюс `:227`, `:705`, `:565`, `:1528`, `:2542`, `:3586` — end-to-end запуски `join(PROJ,"pretool_guard.py")`
и `test_pc_orchestrator.py:2329`/`:2355` — дочерний `python -c "import pc_orchestrator"` с `cwd=o.REPO`
(**ребёнок импортирует живой модуль, даже если родитель импортировал копию** — в исходнике родителя
этого не видно).

---

## §3. Полоса VPS — перечень (по снимку `tmp/srv`, HEAD `7298787` от 01.08.2026)

Числа посчитаны мной на снимке: **149** файлов `test_*.py`, из них **125** кладут абсолютный корень
в `sys.path[0]` (119 прямым литералом `sys.path.insert(0, "/root/turbobaby-manager-bot")` + 6 через
абсолютную `ROOT`: `test_gate_final_alert`, `test_gate_selective`, `test_gate_single_selective`,
`test_no_push_leak`, `test_pretool_commit_msg`, `test_test_noise_isolation`). Ни один `conftest.py`
(`tmp/srv/conftest.py`, `tmp/srv/tests/conftest.py`) `sys.path` не трогает — конкурирующей
относительной вставки нет, `insert(0, …)` выигрывает безусловно.

### 3.1 Хардкод корня в гард-тесте (образец из задания — ПОДТВЕРЖДЁН)

`tests/test_guard_cli_args.py:27` — `ROOT = "/root/turbobaby-manager-bot"` (+ `sys.path.insert` на :15).
**`ROOT` здесь не файл, а `cwd`-аргумент** `PG.classify(cmd, ROOT)` в 10 методах из 19
(:86, :93, :100, :113, :120, :131, :138, :147, :154, :166). Внутри прода этот `cwd` уходит в
`_resolve_script` и в `git -C PROJECT ls-files` — то есть вердикт теста есть функция содержимого
ЖИВЫХ `cclog.py` / `registry_check.py` / `gate.py`. Правка тех же файлов в копии покрасить его не может.

Отдельно, строка **:166** — единственный на обеих полосах случай, когда тест **создаёт файл в корне
живого репозитория**: `tempfile.NamedTemporaryFile(suffix=".py", dir=ROOT, delete=False)`. Убийство
процесса между :168 и `os.unlink` в `finally` (:177) оставляет в живом дереве untracked `.py`, и
имя от `tempfile` не начинается с `_`, то есть под `.gitignore` не прячется.

### 3.2 Три теста надзора за исходником демона (зеркало ПК-образца, тоже три)

| # | файл:строка | как находит исходник | что утверждает |
|---|---|---|---|
| 1 | `tests/test_claim_event.py:242` | `open("/root/turbobaby-manager-bot/orchestrator_daemon.py")` — голый литерал | точка записи `write_claim_event(` ровно одна и лежит внутри `process_new` (:245–246) |
| 2 | `tests/test_guard_marker_ttl.py:216` | голый литерал того же файла | `_env_int("GUARD_MARKER_TTL"` присутствует (:217) |
| 3 | `tests/test_settings_allowlist.py:42` → чтение на `:243` | `DAEMON_SRC = os.path.join(ROOT, "orchestrator_daemon.py")`, `ROOT` — литерал на `:36` | `HEADLESS_SETTINGS = …` объявлен и `"--settings", HEADLESS_SETTINGS` встречается ≥2 раз (:244, :246) |

Правильный контрпример в том же дереве: `tests/test_approve_layers.py:10` и
`tests/test_probe_isolation.py:38` читают ТОТ ЖЕ `orchestrator_daemon.py` от выведенного корня.

### 3.3 Прочее живое (кратко; подробная таблица — перепись подагентом, сверена мной точечно)

- **Читают исходник по абсолютному пути:** `test_fmt.py:48` (живой `splinter.py`),
  `test_odo_confirm_path.py:349` (живой `bot.py`), `test_notify_force.py:75,77`,
  `test_gate_final_alert.py:139` (живой хук `deploy/hooks/pre-push`).
- **Читают живой `.env`:** `test_inbox.py:219`, `test_lane_pc.py:232` — обе под `if os.path.exists`,
  то есть вне VPS **молча зеленеют, ничего не проверив**.
- **Читают живые конфиги разрешений:** `test_settings_allowlist.py:37–41`,
  `test_settings_failopen.py:23–25`, `test_settings_deferred_restart.py:29–30`,
  `test_guard_own_restarts.py:31–34`.
- **Запускают живое:** `test_guard_escalation.py:167` (спавнит `<живой>/venv/bin/python3
  <живой>/tests/test_pretool_probe_dedup.py` с `cwd=_ROOT` — **живой тест как подпроцесс**),
  `test_no_push_leak.py:139` (полный прогон живого `gate.py`), `test_mem_probe.py:22,58,72,78`
  (живой интерпретатор + живой `mem_probe.py`), `test_settings_allowlist.py:250–253`
  (`git check-ignore` в живом репо), `test_pretool_infoflags.py`, `test_pretool_probe_dedup.py`,
  `test_pretool_commit_msg.py`, `test_guard_own_restarts.py:125`, `test_notify_hook_prompt.py:36,83`.
- **Второе живое дерево `/root/turbobaby-bridge-gs`:** `test_booking_gs.py:21`,
  `test_booking_close_gs.py:24–25`, `test_botdata_gs.py:10`, `test_delivery_gs.py:10`,
  `test_quote_caps.py:13`, `test_set_caps.py:15–16` и три харнесса `*_gs_harness.js`
  (`booking:12`, `botdata:15`, `delivery:11–12`). Путь до самого харнесса выведен правильно —
  и это ничего не даёт, потому что цель внутри харнесса абсолютная.

---

## §4. Даёт ли ложно-зелёный на копии ветки — по видам

Ответ зависит от того, ЧТО в тесте приезжает из копии, а что из живого дерева. Копия ветки
импортирует СВОЙ модуль (`sys.path[0]` = каталог теста), но модуль несёт живой корень в константе.
Отсюда смесь, и она разная по видам.

| вид | пример | на копии ветки |
|---|---|---|
| **B1. И код, и данные из живого дерева** | `test_whole_closure_compiles_right_now` (10733): `_dep_files()` живой + `py_compile` живых файлов | **ЛОЖНО-ЗЕЛЁНЫЙ, всегда.** Тест структурно неспособен покраснеть от правки копии: синтаксическую ошибку, внесённую в копию, он не компилирует ни разу. То же — `:10606`, `:10632`, `:10652`, `:10667`, `:10712`, `:10724`; VPS `test_claim_event.py:242`, `test_guard_marker_ttl.py:216`, `test_settings_allowlist.py:243`, `test_fmt.py:48`, `test_odo_confirm_path.py:349` |
| **B2. Голден из копии, факт из живого дерева** | `test_orch_runtime_covers_every_top_import_of_daemon` (822) и `test_dirty_gate_watches…` (10703): `_ORCH_RUNTIME` берётся из КОПИИ, а импорты читаются из ЖИВОГО файла | **ЛОЖНО-ЗЕЛЁНЫЙ в опасную сторону.** Добавь в копию `import newdep` и не впиши его в `_ORCH_RUNTIME` — живой файл такого импорта не содержит, `missing` пуст, тест зелён. Это ровно та регрессия, ради которой тест написан. Обратная сторона: правка `_ORCH_RUNTIME` в копии даёт **ложно-КРАСНЫЙ** |
| **C. Абсолютный `cwd`/абсолютный скрипт у подпроцесса** | ПК `test_pc_orchestrator.py:2329`, `test_pretool_guard.py:227/705/…`; VPS `test_guard_escalation.py:167`, `test_no_push_leak.py:139`, `test_mem_probe.py:58` | **ЛОЖНО-ЗЕЛЁНЫЙ и невидимый в исходнике родителя:** родитель импортировал копию, ребёнок стартует в живом дереве и импортирует живые модули. Читая тест, этого не видно — видно только слово `cwd=` |
| **D. `cwd` как аргумент классификатора** | VPS `test_guard_cli_args.py` (10 методов), `test_guard_тест_entity.py` (7 вызовов `_analyze(cmd, ROOT)`) | **ЛОЖНО-ЗЕЛЁНЫЙ.** Вердикт — функция живых `cclog.py`/`gate.py`, которые прод резолвит по `cwd` и по `git -C PROJECT` |
| **E. Живой прогон как утверждение** | `test_pretool_guard.py:372` (`assertEqual(r.returncode, 0)` на прогоне живой пары модулей) | **Ни зелёный, ни красный — ЧУЖОЙ.** Вердикт копии = зелень живого дерева. При работе над веткой живое дерево почти всегда грязное, значит тест копии красен по чужой причине; и наоборот, зелёное живое дерево оправдывает копию, которую никто не прогонял |
| **F. Живой корень ОТСУТСТВУЕТ (прогон не на своей машине)** | вся полоса VPS с ПК; ПК-тесты из каталога вне `D:` | Два исхода: `FileNotFoundError` → **красный по среде** (замер в памяти: 26 красных); либо ветка под `if os.path.exists(...)` → **вакуумно-зелёный**: прошло, не проверив ничего (`test_inbox.py:219`, `test_lane_pc.py:232`) |
| **G. Запись в живое дерево** | ПК `test_pc_orchestrator.py:304`, `test_pretool_guard.py:3861`; VPS `test_guard_cli_args.py:166` | Вердикт может быть честным, а **побочный эффект — загрязнение живого дерева прогоном копии.** Это не про зелень, это про то, что копия перестаёт быть копией |

**Сводно:** ложно-зелёный дают все виды кроме E и G; у B1 он абсолютный (тест не может покраснеть
никогда), у B2 — направленный (молчит ровно на добавлении, то есть на своём предмете).

---

## §5. Правило, как чинить весь класс (текстом, не применено)

**ПРАВИЛО: корень дерева — это ОТВЕТ НА ВОПРОС «ГДЕ Я», а не константа. Спрашивать его обязан
каждый файл у СВОЕГО `__file__`; передавать его дальше — только параметром.**

Три хода, в этом порядке (порядок существенный: обратный порядок не лечит):

1. **Сначала прод, потом тесты.** Литерал корня в продовом модуле — дефект прода, а не теста:
   `REPO`/`PROJECT` становятся `os.path.dirname(os.path.abspath(__file__))`. Пока
   `pc_orchestrator.py:98`, `pretool_guard.py:281`, `orchestrator_daemon.py:33`,
   `pretool_guard.py:78`, `guard_replay.py:36` держат литерал, починка тестов — косметика: тест,
   написанный безупречно, получит живое дерево через `import`. Исключение, которое остаётся
   литералом СОЗНАТЕЛЬНО и должно быть НАЗВАНО в коде: ссылка на ЧУЖОЕ дерево
   (`_MANAGER_CLONE`, `BRIDGE_GS`) — это не самоссылка, и выводить её от `__file__` неоткуда.

2. **В тесте путь к исходнику строится от `__file__` СВОЕГО файла**, а не от продовой константы.
   Шаблоны уже есть в обоих деревьях, брать их, а не изобретать: скан каталога —
   `test_log_setup.py:504–518` (`os.listdir(REPO)` + `ast`), перечисление через git —
   `test_result_judge_pc.py:615` / `test_series_pc.py:611` (`git ls-files` с `cwd=` выведенного корня),
   подпроцесс — `test_utf8_output_guard.py:37,232` и `test_probe_isolation.py:45,185`,
   VPS-скан исходника демона — `test_approve_layers.py:10,121`. Если тест обязан мерить именно
   продовую константу — он её ПОДМЕНЯЕТ на свой корень (`mock.patch.object(o,"REPO", tmp)`,
   шаблон `test_pc_orchestrator.py:10758`), а не наследует.
   **Отдельным пунктом: `cwd` подпроцесса — тоже корень.** Абсолютный `cwd` переводит ребёнка
   на живое дерево бесшумно, и в исходнике родителя это не читается.

3. **Держать замком, а не дисциплиной.** Правило без проверяющего протухает молча (в этом репо
   это уже записанный класс). Замок — два теста-инварианта на каждой полосе:
   (а) ни один tracked `.py` не содержит литерала СВОЕГО корня вне явного списка исключений с
   причиной; (б) ни один tracked тест не пишет и не запускает ничего по пути выше собственного
   корня.

**Операционный критерий, по которому опознаётся весь класс без грепа** — вопрос на
фальсифицируемость, задаваемый каждому тесту:

> **«Какая правка В КОПИИ красит этот тест?»** Не можешь назвать такую правку — тест меряет
> не своё дерево. Зелёный, который невозможно покрасить правкой измеряемого предмета, — не
> зелёный, а слепой.

Он ловит и то, чего греп по `"/root/"` и `"D:\\"` не видит: наследование через `import`,
абсолютный `cwd` у подпроцесса, `cwd`-аргумент классификатора и ветку `if os.path.exists` вокруг
живого файла (вакуумно-зелёный).

---

## §6. Чем измерено и что НЕ измерено (честно)

**Измерено мной напрямую:** все шесть продовых констант (греп + чтение строк 30–40
`orchestrator_daemon.py`, 90–105 `pc_orchestrator.py`); три ПК-теста надзора (чтение
`test_pc_orchestrator.py:808–857` и `:10500–10735`); `_dep_files()` и `client_contour.closure`
(чтение `pc_orchestrator.py:3960–4030`, `client_contour.py:82`); `test_guard_cli_args.py:27,166–177`;
третий VPS-сканер `test_settings_allowlist.py:36–42,242–248`; обе записи в живое дерево на ПК
(`test_pretool_guard.py:3855–3872`) и живой перепрогон (`:368–378`); числа полосы VPS
(149 файлов / 125 с абсолютным `sys.path`, 119 + 6 — посчитано командой, список шести приведён).

**Не измерено, и это меняет вес выводов:**

1. **Полоса VPS измерена по СНИМКУ, а не по живому дереву.** `tmp/srv` — untracked клон,
   HEAD `7298787` от **01.08.2026**, то есть месяц отставания; mtime файлов — 01.08 03:18–03:20.
   Всё в §3 верно ДЛЯ ЭТОГО СНИМКА. Живая полоса могла и вырасти, и починиться.
2. **`D:\foreign\turbobaby-manager-bot` не читался вовсе** — каталог вне проекта, разрешение на
   чтение в этом заходе не выдано, обхода не искалось. Свериться с ним — отдельный заход.
3. **Ни один тест не запускался.** Выводы §4 получены разбором кода, а не прогоном на копии.
   Прямой замер («скопировать ветку, сломать зависимость в копии, прогнать») сделал бы вид B2
   доказанным, а не выведенным; он не делался — задание запрещало правки, а порча копии ради
   замера потребовала бы записи.
4. **Длинные таблицы §2.3 и §3.3 — перепись подагентами**, сверенная мной точечно (перечислено
   выше). Отдельные строки внутри них не перепроверены построчно.
5. **Числа полосы ПК по охвату наследования** (14 импортёров `pc_orchestrator`, 8 импортёров
   `pretool_guard`, ~55 методов с живым касанием) взяты из переписи подагента и мной не
   пересчитывались.
