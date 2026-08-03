# -*- coding: utf-8 -*-
"""КЛАСС «признак сработал на ТЕКСТЕ, а не на ДЕЙСТВИИ» — корпус регрессий (31.07.2026).

За сутки владелец увидел СЕМЬ ложных карточек одного рода: класс присваивался по форме
команды (подстрока), а не по тому, что команда ДЕЛАЕТ. Здесь каждый случай зафиксирован
дословно — и рядом ДОКАЗАТЕЛЬСТВО ОБРАТНОГО: настоящая операция того же вида по-прежнему
краснеет. Тест обязан падать, если смягчение переехало на настоящее красное.

Файл называется test_*.py осознанно: гард НЕ сканирует содержимое тест-целей
(`_is_test_target`) — иначе собственные фикстуры (`.env`, `os.remove`, `gspread`) красили бы
прогон корпуса. Так же устроен и `test_pretool_guard.py`."""
import io
import sys
import unittest

import pretool_guard as G

NO_ENV = {}      # чужих «да» в замере нет


def verdict(tool, arg):
    """Полный конвейер: decide → decide_for_role → card_decision. → (решение, вид, объект)."""
    if tool in ("Bash", "PowerShell"):
        ti, cmd = {"command": arg}, arg
    elif tool == "NotebookEdit":
        ti, cmd = {"notebook_path": arg}, ""
    else:
        ti, cmd = {"file_path": arg}, ""
    data = {"tool_name": tool, "tool_input": ti, "cwd": G.PROJECT}
    action, kind, obj = G.decide_for_role(data, True, NO_ENV)
    if action == "ask":
        action = G.card_decision(kind, obj, cmd)[0]
    return action, kind, obj


def is_card(tool, arg):
    return verdict(tool, arg)[0] in ("ask", "deny")


# ─────────────────────────── СЕМЬ ЖИВЫХ ЛОЖНЫХ СЛУЧАЕВ ────────────────────────────
# (имя случая, инструмент, команда) — карточки быть НЕ должно ни у одной строки.
FALSE_CASES = [
    # 1. Проверка СУЩЕСТВОВАНИЯ принята за чтение секрета.
    ("1 Test-Path .env", "PowerShell", r'Test-Path D:\turbobaby-bot\.env'),
    ("1 Test-Path if", "PowerShell", r'if (Test-Path "D:\turbobaby-bot\.env") { "есть" } else { "нет" }'),
    ("1 ls .env", "Bash", 'ls -la .env'),
    # 2. Чтение переменных ЖИВОГО ПРОЦЕССА принято за правку окружения.
    ("2 PEB env", "Bash",
     'venv/Scripts/python.exe -c "'
     'import ctypes; ctypes.windll.kernel32.ReadProcessMemory; '
     'ctypes.windll.ntdll.NtQueryInformationProcess; print(\'env из PEB, не из файла .env\')"'),
    # 3. SELECT в режиме только для чтения принят за запись.
    ("3 select ro-uri", "Bash",
     'sqlite3 "file:moderation_ipc.db?mode=ro" "select id,status from tasks limit 5"'),
    ("3 select", "Bash", 'sqlite3 moderation_ipc.db "select count(*) from tasks"'),
    ("3 pragma", "Bash", 'sqlite3 moderation_ipc.db ".schema tasks"'),
    # 4. wc/sed по конфигу приняты за правку конфига.
    ("4 wc конфига", "Bash", 'wc -l .claude/settings.json'),
    ("4 sed конфига", "Bash", "sed -n '20,32p' .claude/settings.json"),
    ("4 jq конфига", "Bash", 'jq .permissions .claude/settings.json'),
    # 5. Присваивание переменной окружения принято за удаление файла.
    ("5 env-префикс", "Bash", 'RM_MODE=1 PYTHONUTF8=1 venv/Scripts/python.exe log_setup.py'),
    ("5 присваивание rm=", "Bash", 'rm_count=0; echo "$rm_count"'),
    ("5 $env: PowerShell", "PowerShell", '$env:RM_DRY="1"; Write-Output $env:RM_DRY'),
    # 6. МИНУС в тексте команды принят за флаг удаления.
    #    ДВЕ КОМАНДЫ ЭТОГО СЛУЧАЯ ПЕРЕЕХАЛИ В `TRUE_RED` 03.08.2026, и это не смягчение теста, а
    #    смена ДОКТРИНЫ: обе сносят файл в КОРНЕ РЕПО (`cowork_log.spool`, `tmp_guard_cards.json`
    #    — имя начинается на `tmp_`, но в каталоге `tmp/` файл НЕ лежит), а красным теперь делает
    #    ЦЕЛЬ ВНЕ ВРЕМЕННЫХ ЗОН, а не «массовость». Прежняя посылка «один файл ⇒ карточки нет»
    #    и была дырой класса. Сама находка случая 6 никуда не делась и проверяется ниже прямо —
    #    `test_force_is_still_not_recursion`: `-Force`/`-f` рекурсией не считаются.
    # 7. Слово в ТЕКСТЕ (шаблон поиска, сообщение, печать, фикстура) принято за операцию.
    ("7 .env в шаблоне grep", "Bash",
     'grep -n -E "\\.env|deny|\\"model\\"|clasp push" .claude/settings.json | head -30'),
    ("7 .env в Select-String", "PowerShell",
     r'Select-String -Path .claude\settings.json -Pattern "\.env"'),
    ("7 .env в печати", "Bash", 'echo "=== .env exists (not reading contents) ==="'),
    ("7 kill в записи журнала", "Bash",
     "printf '%s' 'DONE 17:20: правило карточки — kill по подстроке снят' "
     "| venv/Scripts/python.exe cowork_log_append.py"),
    ("7 rm -rf в git -m", "Bash",
     'git commit -m "гард: убран rm -rf из скрипта уборки, schtasks /change не трогаем"'),
    ("7 фикстура гарда в -c", "Bash",
     'venv/Scripts/python.exe -c "import pretool_guard as g; '
     'print(g.decide({\'tool_name\':\'Bash\',\'tool_input\':'
     '{\'command\':\'Stop-Process -Id 11168 -Force\'}}))"'),
    ("7 schtasks в подписи", "Bash", 'echo "---SCHTASKS XML---"; ls docs/artifacts/*.xml'),
]

# ────────────────── ДОКАЗАТЕЛЬСТВО ОБРАТНОГО: настоящее красное ───────────────────
# Карточка (ask или deny) обязана быть у КАЖДОЙ строки.
TRUE_RED = [
    ("настоящее удаление rm -rf", "Bash", 'rm -rf docs/artifacts/journal/'),
    ("настоящее удаление Recurse", "PowerShell", r'Remove-Item D:\turbobaby-bot\docs -Recurse -Force'),
    ("настоящее удаление по маске", "Bash", 'rm docs/artifacts/*.md'),
    ("настоящая запись в БД", "Bash",
     'sqlite3 moderation_ipc.db "update tasks set status=\'done\' where id=7"'),
    ("настоящая запись в БД (py)", "Bash",
     'venv/Scripts/python.exe -c "import sqlite3; '
     'sqlite3.connect(\'moderation_ipc.db\').execute(\'delete from tasks\')"'),
    ("настоящая правка конфига cp", "Bash", 'cp .claude/settings.json.new .claude/settings.json'),
    ("настоящая правка конфига >", "Bash", 'echo "{}" > .claude/settings.json'),
    ("настоящая правка конфига через bash -c", "Bash", 'bash -c "cat x > .claude/settings.json"'),
    ("настоящая правка конфига Write", "Write", r'D:\turbobaby-bot\.claude\settings.json'),
    ("настоящее чтение секрета cat", "Bash", 'cat .env'),
    ("настоящее чтение секрета PS", "PowerShell",
     r'Get-Content D:\turbobaby-bot\.env | Select-String MODEL'),
    ("настоящее чтение секрета grep", "Bash", 'grep -n BRIDGE_TOKEN .env'),
    ("настоящее чтение секрета Read", "Read", r'D:\turbobaby-bot\.env'),
    ("настоящая правка секрета Write", "Write", r'D:\turbobaby-bot\.env'),
    ("секрет ПОСЛЕ пробы наличия", "Bash", 'Test-Path .env && cat .env'),
    ("настоящий kill по PID", "PowerShell", 'Stop-Process -Id 1112 -Force'),
    ("настоящий taskkill", "Bash", 'taskkill /PID 4242 /F'),
    ("настоящий schtasks /change", "Bash", 'schtasks /change /tn TurboBabyRC /disable'),
    ("настоящий git push --force", "Bash", 'git push --force origin main'),
    ("настоящий git reset --hard", "Bash", 'git reset --hard HEAD~3'),
    ("настоящий живой лист", "Bash",
     'venv/Scripts/python.exe -c "import gspread; gspread.open(\'Лист1\').append_row([1])"'),
    ("настоящий выход в сеть", "Bash", 'curl -s https://example.com/x'),
    ("настоящий clasp push", "Bash", 'clasp push -f'),
    ("настоящая запись вне репо", "Bash", r'echo x > C:\Windows\Temp\evil.txt'),
    # ЗЕРКАЛО КЛАССА VPS (03.08.2026): удаление вне временных каталогов. Замер живого гарда нашёл
    # ДВЕНАДЦАТЬ форм, проходивших молча; здесь по одной на каждую причину молчания.
    ("удаление одного файла репо", "Bash", 'rm suggest.py'),                 # «не массовое»
    ("удаление одного файла PS", "PowerShell", r'Remove-Item D:\turbobaby-bot\cowork_log.spool -Force'),
    ("удаление алиасом ri", "Bash", 'rm -f tmp_guard_cards.json'),
    ("удаление через unlink", "Bash", 'unlink suggest.py'),                  # вид не назначался
    ("удаление через shred", "Bash", 'shred -u suggest.py'),
    ("удаление предикатом find", "Bash", 'find docs -name "*.md" -delete'),
    ("удаление find -exec", "Bash", 'find . -name "*.pyc" -exec rm {} \\;'),
    ("удаление через git rm", "Bash", 'git rm suggest.py'),                  # ранний зелёный шорткат
    ("удаление git rm с коммитом", "Bash", 'git rm docs/ENV_PLAYBOOK.md && git commit -m x'),
    ("удаление корня репо", "Bash", 'rm -rf D:/turbobaby-bot'),
    ("удаление маской в tmp", "Bash", 'rm tmp/*.log'),                       # там бэкапы brain_writer
    ("удаление без названной цели", "Bash", 'find . -name "*.pyc" -print0 | xargs -0 rm'),
]


class FalseCards(unittest.TestCase):
    """Семь живых случаев владельца: класс по ТЕКСТУ. Карточки быть не должно."""


class TrueRed(unittest.TestCase):
    """Доказательство обратного: настоящая операция краснеет как прежде."""


def _mk_false(name, tool, cmd):
    def t(self):
        act, kind, obj = verdict(tool, cmd)
        self.assertNotIn(act, ("ask", "deny"),
                         "ЛОЖНАЯ карточка %s: %s/%s на %r" % (name, act, kind, cmd))
    return t


def _mk_true(name, tool, cmd):
    def t(self):
        act, kind, obj = verdict(tool, cmd)
        self.assertIn(act, ("ask", "deny"),
                      "НАСТОЯЩЕЕ КРАСНОЕ проехало молча (%s): %s/%s на %r"
                      % (name, act, kind, cmd))
    return t


for _i, (_n, _t, _c) in enumerate(FALSE_CASES):
    setattr(FalseCards, "test_false_%02d" % _i, _mk_false(_n, _t, _c))
for _i, (_n, _t, _c) in enumerate(TRUE_RED):
    setattr(TrueRed, "test_red_%02d" % _i, _mk_true(_n, _t, _c))


class DeleteOutsideTemp(unittest.TestCase):
    """ЗЕРКАЛО КЛАССА VPS (03.08.2026): удаление вне временных каталогов проходило молча.

    Корпус выше держит ВЕРДИКТ (красное/зелёное), а здесь — три свойства, из-за которых класс
    и родился: уборка в `tmp/` осталась зелёной, находка случая 6 жива, и разбор у класса СВОЙ."""

    def test_cleanup_in_temp_stays_green(self):
        """ГРАНИЦА ПРАВКИ: уборка своих черновиков подтверждения по-прежнему не стоит."""
        for cmd in ('rm -rf tmp/bridge_gs',
                    'rm -rf D:/turbobaby-bot/tmp/bridge_v75',
                    'rm tmp/a.json tmp/b.json',
                    'rm -rf tmp/srv-audit 2>/dev/null',
                    'cd "$LOCALAPPDATA/Temp/claude/D--turbobaby-bot/29ab/scratchpad" && '
                    'rm -f token.txt live.txt'):
            with self.subTest(cmd=cmd):
                self.assertFalse(is_card("Bash", cmd), "уборка в tmp покраснела: %r" % cmd)

    def test_backslash_paths_survive_the_split(self):
        """POSIX-режим `shlex` СЪЕДАЛ обратный слэш: `D:\\turbobaby-bot\\tmp\\x` приезжал целью
        `D:turbobaby-bottmpx`, и такой путь не совпадал ни с одной зоной. На этой машине основной
        шелл — PowerShell, то есть так набирается БОЛЬШИНСТВО путей, и уборка в `tmp/` из него
        считалась удалением вне временных каталогов. Обе стороны ниже — одним разбором."""
        self.assertEqual(G._delete_scan(r'Remove-Item D:\turbobaby-bot\tmp\x -Force')[0],
                         [r'D:\turbobaby-bot\tmp\x'])
        for green in (r'Remove-Item D:\turbobaby-bot\tmp\x -Force',
                      r'Remove-Item D:\turbobaby-bot\tmp\bridge -Recurse -Force',
                      r'rd /s /q D:\turbobaby-bot\tmp\srv'):
            with self.subTest(cmd=green):
                self.assertFalse(is_card("PowerShell", green), green)
        # …и обход `..` доверия не получает — послабление зоной, а не строкой пути.
        for red in (r'Remove-Item D:\turbobaby-bot\suggest.py -Force',
                    r'Remove-Item D:\turbobaby-bot\tmp\..\suggest.py -Force'):
            with self.subTest(cmd=red):
                self.assertTrue(is_card("PowerShell", red), red)

    def test_powershell_provider_item_is_not_a_file(self):
        """ЖИВЫЕ СТРОКИ ЖУРНАЛА (5 за неделю, headless-прогоны тестов): `Remove-Item Env:\\X`
        снимает ПЕРЕМЕННУЮ ОКРУЖЕНИЯ, файла не трогает. Правка «цель вне временных зон →
        красное» покрасила бы их все, если бы судила глагол, а не цель."""
        for cmd in (r'Remove-Item Env:\STEP_SELFHEAL',
                    r'Remove-Item Env:\PC_MUTE_ENTRYPOINTS',
                    r'Remove-Item Variable:\tmpvar'):
            with self.subTest(cmd=cmd):
                self.assertFalse(is_card("PowerShell", cmd), cmd)
        # …но файл в той же строке краснеет: послабление касается ТОЛЬКО элемента провайдера.
        self.assertTrue(is_card("PowerShell", r'Remove-Item Env:\X; rm suggest.py'))

    def test_force_is_still_not_recursion(self):
        """НАХОДКА СЛУЧАЯ 6 ЖИВА (31.07.2026): `-Force` рекурсией не является — буква `r` стоит
        в середине слова. Две его команды переехали в `TRUE_RED` по ЦЕЛИ, а не по рекурсии,
        поэтому свойство проверяется здесь прямо, иначе оно осталось бы без сторожа."""
        for flag in (" -Force ", " -f ", " -Confirm ", " -Verbose ", " -ErrorAction "):
            with self.subTest(flag=flag):
                self.assertFalse(G._RE_DEL_RECURSE.search(flag), flag)
        for flag in (" -rf ", " -r ", " --recursive ", " /s ", " -Recurse "):
            with self.subTest(flag=flag):
                self.assertTrue(G._RE_DEL_RECURSE.search(flag), flag)
        self.assertEqual(G._delete_scan(r'Remove-Item D:\turbobaby-bot\cowork_log.spool -Force'),
                         ([r'D:\turbobaby-bot\cowork_log.spool'], False, False))

    def test_class_parses_the_raw_command_not_the_scan_text(self):
        """ПОЧЕМУ РАЗБОР СВОЙ. Общий скан-текст режет аргументы скрипта вместе с ДЕЙСТВИЕМ:
        предикат `-delete` и маска из него исчезают целиком. Замер 03.08, дословно."""
        eaten = 'venv/Scripts/python.exe tools/clean.py --root docs -delete "*.md"'
        self.assertNotIn("-delete", G._scan_text(eaten))
        # …поэтому решение читает СЫРУЮ команду: там предикат на месте и цель находится.
        self.assertEqual(G._delete_reach('find docs -name "*.md" -delete'), "docs")
        self.assertTrue(G._delete_stays_red('find docs -name "*.md" -delete'))
        # Упоминание глагола в тексте разбор не обманывает — он структурный, а не подстрочный.
        self.assertIsNone(G._delete_reach('git commit -m "убрал rm -rf из уборки"'))
        self.assertIsNone(G._delete_reach('grep -rn "unlink" pretool_guard.py'))

    def test_unnamed_target_is_denied_not_asked(self):
        """FAIL-CLOSED: список файлов не существует до исполнения — подтверждать нечего."""
        for cmd in ('find . -name "*.pyc" -print0 | xargs -0 rm',
                    'Get-ChildItem docs -Filter *.md | Remove-Item -Force'):
            with self.subTest(cmd=cmd):
                act, kind, obj = verdict("Bash", cmd)
                self.assertEqual((act, kind), ("deny", "delete"), cmd)
                self.assertEqual(obj, G.DEL_TARGET_UNKNOWN)


def table():
    out = sys.stdout
    try:
        out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    bad_f = bad_r = 0
    out.write("\n===== ЛОЖНЫЕ (карточки быть НЕ должно) =====\n")
    for n, t, c in FALSE_CASES:
        a, k, o = verdict(t, c)
        ok = a not in ("ask", "deny")
        bad_f += 0 if ok else 1
        out.write("  %s %-26s %-8s %-12s obj=%s\n"
                  % ("ok  " if ok else "ЛОЖЬ", n, a, k or "-", o or "-"))
    out.write("\n===== НАСТОЯЩЕЕ КРАСНОЕ (карточка обязана быть) =====\n")
    for n, t, c in TRUE_RED:
        a, k, o = verdict(t, c)
        ok = a in ("ask", "deny")
        bad_r += 0 if ok else 1
        out.write("  %s %-30s %-8s %-12s obj=%s\n"
                  % ("ok  " if ok else "ДЫРА", n, a, k or "-", o or "-"))
    out.write("\nИТОГ: ложных краснеет %d из %d; настоящего красного проехало %d из %d\n"
              % (bad_f, len(FALSE_CASES), bad_r, len(TRUE_RED)))
    out.flush()
    return bad_f, bad_r


if __name__ == "__main__":
    if "--table" in sys.argv:
        table()
    else:
        unittest.main()
