# Доставка накопленного демону: обе премисы ФОРМЫ не подтвердились (10.08.2026)

Цель ТЗ: перезапустить `orchestrator-daemon`, чтобы живой процесс поднял 5629a6c и a59ce6e.
Форма ТЗ: «перезапуск ВЫПОЛНЯЙ обычной командой, синхронно; systemd-run НЕ применять —
исполнитель обязан увидеть результат своими глазами; гард сам перехватит команду и выпишет
владельцу карточку».

**Перезапуск НЕ выполнен.** Обе премисы, на которых стоит форма, проверены и обе ложны.
Операция при этом законна (оранжевая) — не сделан именно ЭТОТ способ её сделать.

## 1. Состояние ДО (шаги 1–2 ТЗ выполнены, расхождений нет)

```
$ git -C /root/turbobaby-manager-bot rev-parse HEAD
df7a139b977fe1a4ecec042c7241a488bbf9a7fb

$ git -C /root/turbobaby-manager-bot rev-parse origin/main
df7a139b977fe1a4ecec042c7241a488bbf9a7fb

$ git -C /root/turbobaby-manager-bot status --porcelain --untracked-files=no
(пусто)

$ systemctl show orchestrator-daemon -p MainPID -p ActiveState -p SubState -p ExecMainStartTimestamp
ActiveState=active
SubState=running
MainPID=822554
ExecMainStartTimestamp=Mon 2026-08-10 08:08:56 UTC

$ git -C /root/turbobaby-manager-bot log --format='%H %cI %s' -n 4
df7a139b977fe1a4ecec042c7241a488bbf9a7fb 2026-08-10T20:31:29Z О3: причина «на диске лежит не то» названа, вердикт «неизвестно» не тронут
21eb474676bbc0ec9714a32ebc562e49300cc8d3 2026-08-10T19:46:58Z сьют серии сам заявляет свою изоляцию: премиса ТЗ не подтвердилась
a59ce6ee683831982df107c11daff8f160846fce 2026-08-10T19:35:55Z состояние серии живёт в файле демона, а сверка умеет краснеть
5629a6c892b8211f9224cec5842dea2235db34c4 2026-08-10T18:24:19Z живой счёт серии цепочек: работа оборванной задачи 451 закреплена как есть
```

Шаг 2 пройден: дерево по отслеживаемым файлам чистое, HEAD == origin/main. Останавливаться
по шагу 2 было не на чем — остановка ниже вызвана другим.

Основание ТЗ подтвердилось независимо и живым фактом (это не переизмерение, а побочный
результат поиска отпечатка):

```
$ journalctl -u orchestrator-daemon --since "2026-08-10 08:08:56" --no-pager -g "СЕРИЯ" -n 5
-- No entries --
(exit 1)
```

Демон пишет `log.info("СЕРИЯ: %s", …)` (`orchestrator_daemon.py:4920`) на каждом терминале
цепочки. За 12 часов работы — ни одной строки: кода счёта серии в памяти процесса НЕТ.

## 2. Премиса «исполнитель увидит результат своими глазами» — ЛОЖНА

Исполнитель этой задачи — не наблюдатель перезапуска, а его ЖЕРТВА.

```
$ ps -o pid,ppid,pgid,lstart,cmd -p 895847
    PID    PPID    PGID                  STARTED CMD
 895847  822554  822554 Mon Aug 10 20:57:20 2026 /usr/bin/claude -p --model claude-opus-5 …

$ systemctl status orchestrator-daemon --no-pager --lines=0
     CGroup: /system.slice/orchestrator-daemon.service
             ├─822554 …/venv/bin/python3 …/orchestrator_daemon.py
             ├─895847 /usr/bin/claude -p --model claude-opus-5 …
             ├─896079 /bin/bash -c "… systemctl status orchestrator-daemon …"
             └─896099 systemctl status orchestrator-daemon --no-pager --lines=0

$ systemctl show orchestrator-daemon -p KillMode -p TimeoutStopUSec -p Delegate -p ExecStart
TimeoutStopUSec=1min 30s
ExecStart={ … argv[]=…/python3 …/orchestrator_daemon.py … pid=822554 … }
Delegate=no
KillMode=control-group
```

Три факта складываются в одно следствие:

* мой claude-процесс **895847** имеет **PPID 822554** — это сам демон;
* он лежит **внутри cgroup** `/system.slice/orchestrator-daemon.service`;
* `KillMode=control-group` — стоп-фаза шлёт SIGTERM **всему** cgroup, а не одному главному.

Значит `systemctl restart orchestrator-daemon`, выполненный синхронно из тела задачи, убивает
СЕБЯ первым же действием. Пропадает не только «взгляд своими глазами» (шаг 4), но и:
вторая строка журнала (шаг 5), «дословный вывод КАЖДОЙ команды» и сводка (раздел ОТЧЁТ),
блок FACT. Работу собирает `complete_task` того самого демона, который в этот момент
перезапускается, — карточку в 328 писать станет некому, и задача доживёт до реапера сирот
(`ORPHAN_TTL`) с ⏱-диагнозом.

Это ровно тот класс, ради которого в CLAUDE.md заведено ПРАВИЛО САМОМОДИФИКАЦИИ (урок задачи
43, усиленный фиксами 105 и 122): «Прямой `systemctl restart orchestrator-daemon` из тела
headless-задачи — ЗАПРЕЩЁН». Цена подчинения букве формы — не риск, а гарантия: проверка
шага 4 не «может не получиться», она невозможна ПО УСТРОЙСТВУ.

## 3. Премиса «гард сам перехватит команду» — ЛОЖНА (проверено кодом)

```
pretool_guard.py:878   _PROTECTED_PROCS = ("splinter", "orchestrator_daemon", "userbot", …)
pretool_guard.py:880   _HARD_VERBS  = {"kill", "stop"}
pretool_guard.py:881   _GREEN_VERBS = {"restart", "start"}   # свои сервисы: штатный поток → defer

pretool_guard.py:2203-2210
        elif name == "systemctl":
            verb, names = _systemctl_parts(args)
            tgt = _protected_in(names)
            if verb in _HARD_VERBS and tgt:
                return "block", "systemctl " + verb + " " + tgt
            # restart|start своих → ЗЕЛЁНОЕ (в2): штатный поток, гейт живёт выше
            if verb in _HARD_VERBS or (verb in _SVC_VERBS and tgt and verb not in _GREEN_VERBS):
                red = red or ("red", …)
```

`orchestrator-daemon` нормализуется в `orchestrator_daemon` и в защищённых ЕСТЬ, но глагол
`restart` стоит в `_GREEN_VERBS`: первое условие мимо (`restart` не жёсткий глагол), второе
мимо (`verb not in _GREEN_VERBS` ложно). Итог — `green` → **defer**. Карточки владельцу не
будет, перехвата не будет: команда уйдёт на исполнение.

То же говорят обе стороны доктрины: CLAUDE.md держит рестарт своих сервисов в allow и прямо
запрещает объявлять на него NEEDS_APPROVAL, и преамбула исполнителя велит делать его САМ
оранжевым циклом. Расчёт ТЗ на «гард остановит, а я посмотрю» не сбудется ни в одной точке:
гард пропускает, и именно поэтому исполнитель умирает.

## 4. Почему не выбран ни один обход

* **Отложенный `systemd-run`** — штатная форма CLAUDE.md, отчёт бы уцелел; но ТЗ запретило её
  прямым текстом, а явный запрет владельца бьёт остальное (ENV_PLAYBOOK, правило 5). И шага 4
  она всё равно не даёт: процесс гибнет через 10 с, после отчёта.
* **Отчёт вперёд, перезапуск в конец** (буква формы + спасение текста в журнал) — даёт цель,
  но шаг 4 остаётся невыполненным, задача закрывается ⏱-сиротой, а этот обрыв попадёт
  «необъяснённым отказом контура» в счёт серии — ту самую метрику, ради которой едут оба
  коммита. Портить измерение в день его рождения — цена выше выигрыша.
* **Уход из cgroup** (перенос PID, `systemctl kill`) — за пределами мандата; `kill|stop` по
  защищённому имени и так жёсткий блок гарда.

## 5. Что осталось сделать и чем это проверяется

Операция одна и она законна — нужен исполнитель, который переживёт стоп-фазу: владелец из
Termux либо конверт одобренной заявки.

```
systemctl restart orchestrator-daemon
```

Проверка (шаг 4 ТЗ) — для того, кто выполнит:

1. `systemctl show orchestrator-daemon -p ActiveState -p MainPID -p ExecMainStartTimestamp` —
   `active`, старт позже 19:35:55Z (обоих коммитов).
2. **Отпечаток загруженного кода, а не PID:**
   `journalctl -u orchestrator-daemon --since <новый старт> --no-pager -g "СЕРИЯ"` — строка
   `СЕРИЯ:` появляется только из кода a59ce6e/5629a6c (`orchestrator_daemon.py:4920`).
   Сегодня за 12 ч таких строк ноль — до/после различимы однозначно.
3. Файл состояния `chain_series.json` (сейчас 646 байт, mtime 10 Aug 19:30 — тот самый
   нулевой) обязан ожить: демон отдаёт боевой путь только процессу с `_IS_DAEMON`.
4. Сверка `chain_series_report.py --verify` обязана ответить ЧИСЛОМ и кодом 0/1, а не
   «сверять нечего» (код 2), и назвать текущую серию и лучшую.

## 6. Границы захода

Правок кода нет, коммита нет, пуша нет, полный гейт не гонялся. Порогов О3 не касался.
`_scratch_series_iso_0810` не тронут. `turbobaby-bridge-gs` не тронут. В рабочие таблицы не
писал. Клиентского контура не касался. Ничего не удалял. Секретов и конфигов не читал —
свойства юнита сняты запросом `systemctl show`, файл юнита не открывался.
Изменено за заход РОВНО две вещи: две строки в журнале мозга и этот файл.

**НЕ СКЛЕИВАЛ:** ничего, кроме демона, перезапускать не требовалось и не предлагается —
splinter и wa-webhook этой цели не касаются.
