# VPS git-состояние `/root/turbobaby-manager-bot` — разбор + план (read-only)

Снято одним ssh-вызовом (`-i ~/.ssh/turbobaby_vps root@5.223.94.179`), 2026-07-24 **11:10 UTC** (18:10 +07). Ничего не менялось.

## Что мешает авто-фетчу (прямой диагноз)

`git fetch` САМ по себе **работает** — `FETCH_HEAD` обновлён 03:49 UTC (~7 ч назад), origin/main доехал до `7af280c`. Заблокирована не выборка, а **интеграция (pull/rebase)**:

1. **Ветка РАЗОШЛАСЬ:** `## main...origin/main [ahead 7, behind 1]`. `pull --ff-only` не может перемотать разошедшуюся ветку; `pull --rebase/--merge` откажется на грязном дереве.
2. **Дерево грязное:** 4 отслеживаемых файла ` M` + 7 неотслеживаемых `??`.
3. **Планировщика нет:** ни `crontab`, ни `systemd`-таймера с fetch/pull/git/sync — «авто-фетч» встроен в приложение (devbot) и, судя по всему, сам отступает при `diverged+dirty`.

⇒ 1 удалённый коммит `7af280c` висит неинтегрированным, а 7 локальных — незапушенными. Пока дерево грязное и ветка разошлась, авто-синк не проедет.

## Незакоммиченное — 3 группы (`git status --porcelain`)

### A. Нужный код (закоммитить)
- `tests/test_guard_cli_args.py` (`??`) — реальный тест в `tests/`, привязан к локальному HEAD `f185099` («покрытие … + test_guard_cli_args»). Сейчас НЕ в индексе — рискует потеряться при rebase.

### B. Мусор (удалить — все неотслеживаемые скретчи)
- `_check_358_366.py`, `_check_full_texts.py` — ad-hoc проверки
- `_guard_argv_demo.py` — демо
- `_wt_full_test.py`, `_wt_guard_test.py` — throwaway-тесты
- `fix_strip.diff` — сохранённый дифф-побочка (strip-функции гарда)

### C. Непонятное / рантайм-данные (решить, НЕ discard вслепую)
- `spend_ledger.json`, `wallet_cache.json`, `verified_facts.json` (` M`, tracked) — **рантайм-состояние бота** (деньги/кэш/факты). Их правки — это churn состояния, а не код. ⚠️ discard `spend_ledger.json` = потеря финансового следа. Правильно: **`git rm --cached` + внести в .gitignore** (файл на диске живёт как источник правды, git перестаёт пачкаться) ЛИБО закоммитить каноничное состояние. Это же снимает главную причину «грязного дерева» на будущее.
- `_pcport185` (` M`, tracked) — назначение неясно (underscore, «pcport», 185=номер шага?). Прочитать содержимое перед решением; вероятно — трекнутый по ошибке скретч → `git rm --cached` + gitignore или удалить.

## Стеши (`git stash list`) — 4 шт.
- `stash@{0} guard-before-land` — бэкап гарда перед приземлением
- `stash@{1} vps-local-24.07` — сегодняшний VPS-local WIP
- `stash@{2} local-wip-guard-doctrinal-20260723` — гард-doctrinal WIP 23.07
- `stash@{3} WIP 187/194 (OOM-обрыв): _POPEN + guard-block + 4 теста — ВЕКТОР КАСКАДА claude (мисмок subprocess.run), снят 17.07` — совпадает с памятью `vps-claude-cascade-class` (там значился stash@{0}, сдвинут вниз тремя новыми). Корень каскада уже закрыт fixture-guard `6e7e726` ⇒ вероятно **устарел**.

Оценка: {0,1,2} — гард-WIP, скорее всего перекрыты уже приземлёнными `f185099` (локально) и `7af280c` (origin); {3} — устарел. Перед дропом сверить `git stash show -p` каждого; оставить только уникальное неприземлённое.

## .gitignore — есть, дословно снят (см. вывод сессии, секция 6)
Покрывает: `.env*`, `*.session`, `memory.db/*.sqlite*`, `venv/`, `__pycache__/`, `*.log`, выгрузки, IDE/ОС. **НЕ покрывает** рантайм-JSON (`spend_ledger/verified_facts/wallet_cache` — они tracked), скретчи `_*.py`, `*.diff`. Отсюда и грязь.

## План приведения в порядок (НЕ выполнять — жду решения)
1. **Страховка:** `git branch backup/main-20260724` перед любой перезаписью истории.
2. **Спасти код:** `git add tests/test_guard_cli_args.py` → отдельный коммит (чтобы не потерять при rebase).
3. **Снести мусор:** превью `git clean -n`, затем удалить 6 файлов группы B.
4. **Рантайм-данные (группа C):** для `spend_ledger/verified_facts/wallet_cache/_pcport185` — `git rm --cached` + добавить в .gitignore (файлы на диске сохранить). Дерево станет чистым, churn перестанет блокировать pull впредь.
5. **Свести расхождение:** `git fetch` → `git rebase origin/main` (ожидается КОНФЛИКТ в гарде: локальное удаление `_strip_all_git_msgs` в `f185099` vs удалённое добавление `_strip_script_cli_args` в `7af280c` — сверить с памятью [[vps-wip-229-230-branch]]) → прогнать `test_pretool_guard` → `git push` (7→0 ahead, 1→0 behind).
6. **Стеши:** `git stash show -p` каждого; дропнуть устаревший `{3}` и перекрытые `{0,1,2}`; сохранить уникальное.
7. **Безопасность:** ротировать GitHub PAT (он всплыл в выводе `git config`), убрать из `remote.origin.url` в credential-helper или перейти на SSH-remote.

**Порядок обязателен:** сперва чистое дерево (2–4), потом rebase (5) — иначе rebase не стартует.

## Исполнение ШАГ 1 из 2 (25.07, один ssh; история не переписана, стеши целы, без push)

`/root/turbobaby-manager-bot@main`, HEAD было `56fbd69`:
- **Commit `1f6741b`** «tests: guard cli args coverage» — `tests/test_guard_cli_args.py` (181 стр.) спасён в индекс.
- **Удалены 6 скретчей** (group B, все `??` untracked): `_check_358_366.py`, `_check_full_texts.py`,
  `_guard_argv_demo.py`, `_wt_full_test.py`, `_wt_guard_test.py`, `fix_strip.diff`.
- **`git rm --cached`** (файлы на диске сохранены): `spend_ledger.json`, `verified_facts.json`,
  `wallet_cache.json`, `_pcport185`. Сюрприз: `_pcport185` — **gitlink-сабмодуль** (`mode 160000`),
  снят из индекса, каталог на диске не тронут.
- **.gitignore** дополнен блоком (рантайм-JSON по именам + `_*.py`, `*.bak`, `*.diff`, `*.lock`),
  закоммичен **`651db0a`** «chore: ignore runtime state and scratch files» (5 files, +12/−35).
- **Стеши** 4 шт. — идентичны до/после (не тронуты). Только 2 новых коммита, без rebase/reset/amend, без push.

**AFTER `git status --porcelain` НЕ пуст — ровно 1 остаток (дрейф):**
`?? orchestrator_daemon.py.bak-metrics-20260724` — бэкап, появившийся ПОСЛЕ снятия плана (11:10 UTC),
в списке задачи его не было ⇒ не удалял (вне разрешённого набора). `*.bak` его не ловит: имя
оканчивается на `.bak-metrics-20260724`, а не на `.bak`. Развилка владельцу: **(a)** удалить как
одноразовый бэкап, либо **(b)** расширить ignore до `*.bak*`. Остальное дерево чисто — можно к ШАГ 2 (rebase).

## Хвост убран (25.07, один ssh): дерево ЧИСТОЕ

Выбран вариант (b)+(a) сразу:
- `.gitignore` строка 51: `*.bak` → `*.bak*` (ловит датированные суффиксы `.bak-metrics-…`),
  закоммичено **`1bc3051`** «chore: ignore dated backup suffixes» (1 file, +1/−1).
- Удалён `orchestrator_daemon.py.bak-metrics-20260724` (241 866 б, от 24.07 15:52). Проверено по факту:
  **sha256 бэкапа `09c977cb…` = `HEAD~3:orchestrator_daemon.py`** (пре-metrics версия) ⇒ содержимое в
  истории, потеря нулевая (HEAD/~1/~2 держат metrics-версию `818469ee…`).
- **AFTER `git status --porcelain` = ПУСТО.** Стеши 4 шт. целы. История: 3 новых коммита
  (`1f6741b`→`651db0a`→`1bc3051`) поверх `56fbd69`, без rebase/reset/amend, без push.

Итог: дерево VPS **чистое** ⇒ пункт 5 плана (fetch → rebase origin/main → `test_pretool_guard` → push;
ожидается конфликт в гарде, см. [[vps-wip-229-230-branch]]) разблокирован — исполнять по отдельному решению.
