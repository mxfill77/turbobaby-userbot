# RC: ПК не виден в списке устройств приложения (диагностика 24.07.2026, read-only)

Задача из темы 328: коннектор жив, но ПК не появляется в списке устройств приложения.
Все команды выполнены read-only, ничего не останавливалось.

## 1. Пользователи процессов (дословно)

```
PID=18784 Name=claude.exe Owner=mxfillpc\mxfill1 Start=07/24/2026 05:04:23
CommandLine=C:\Users\mxfill1\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.217\claude.exe --remote-control turbobaby-pc

PID=23116 PPID=23284 Name=python.exe Owner=mxfillpc\mxfill1 Start=07/22/2026 22:35:31
CMD="D:\turbobaby-bot\venv\Scripts\python.exe"  "D:\turbobaby-bot\rc_supervisor.py"
```

Оба процесса — под ОДНИМ пользователем `mxfillpc\mxfill1`. Несовпадения пользователей нет.

## 2. where.exe claude

Обычный пользователь (mxfill1, без повышения; `whoami` → `mxfillpc\mxfill1`, элевация False):

```
INFO: Could not find files for the given pattern(s).
exit=1
```

От администратора запустить headless нельзя (UAC), но факт снят через реестр: PATH машины
(HKLM, 18 сегментов — его видит и админ) и PATH пользователя (HKCU, 11 сегментов) каталога
claude НЕ содержат ни одного → от админа `where.exe claude` даст ту же самую ошибку.
`claude doctor` это подтверждает предупреждением: «Native installation exists but
C:\Users\mxfill1\.local\bin is not in your PATH» и «claude command at
C:\Users\mxfill1\.local\bin\claude.exe missing or broken». На работу RC это НЕ влияет:
rc_supervisor резолвит версионный путь сам (resolve_claude), что и видно в CommandLine.

## 3. rc_remote_control.log: слова device/register/account/organization/unauthorized/expired

**Ноль вхождений** — ни одного из шести слов в логе нет (rg -i по всему файлу).
Про регистрацию/вход там есть ДРУГИЕ строки (дословно, серия 22.07 21:35:15–21:55:08):

```
2026-07-22 21:35:15,994 | Remote Control НЕДОСТУПЕН: нет входа — сессию НЕ поднимаю …
2026-07-22 21:35:15,994 | Remote Control НЕДОСТУПЕН: Sign-in is missing the user:profile scope — …
2026-07-22 21:37:03,042 | Remote Control НЕДОСТУПЕН: Remote Control requires; Not signed in to claude.ai; missing the user:profile scope; subscription auth not active — …
… (повторы каждые 15–300 с до 21:55:08)
```

Дальше — после перелогина владельца:

```
2026-07-22 22:35:31,895 | супервизор стартовал (pid=23116, сессия='turbobaby-pc', cwd=D:\turbobaby-bot)
2026-07-22 22:35:33,412 | старт сессии: …\claude-code\2.1.217\claude.exe --remote-control turbobaby-pc
2026-07-24 05:04:05,940 | сессия завершилась (exit=4294967295) — рестарт через 15 с
2026-07-24 05:04:23,829 | старт сессии: …\claude-code\2.1.217\claude.exe --remote-control turbobaby-pc
```

После рестарта 05:04:23 строк «НЕДОСТУПЕН» НЕТ → пре-флайт (`claude doctor`, маркеры
RC_BLOCKERS) прошёл. Лог с 05:04:23 не пополнялся — по логике супервизора сессия жива.
`rc_session_debug.log` стар (LastWrite 22.07 20:00, ДО перелогина) — флага `rc_debug.flag`
сейчас нет, текущая сессия дебаг не пишет.

## 4. claude auth status (без токена, дословно)

```
{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  "email": "1turbobaby@gmail.com",
  "orgId": "30c614de-e141-42da-8975-8b6ca4acd62e",
  "orgName": "1turbobaby@gmail.com's Organization",
  "subscriptionType": "max"
}
```

`claude doctor` сейчас (24.07, тот же бинарь 2.1.217): блокеров RC нет, секция
«Remote Control / Control this session from claude.ai/code or the Claude mobile app»
показана как доступная. Но: «Last update attempt: failed (install_failed) — 2026-07-22»,
локально только 2.1.215 и 2.1.217 (обе от 22.07) — CLI застрял на 2.1.217 два дня.

## 5. Вывод: регистрация или канал?

**Регистрация на стороне аккаунта НЕ является блокером**: вход валиден (max, нужный
аккаунт), скоуп-проблема 22.07 закрыта перелогином, пре-флайт при старте текущей сессии
прошёл. Мешает НЕ auth.

Наиболее вероятное объяснение — класс «живой процесс при мёртвом канале», который
пре-флайт по построению ловит только ДО старта: предыдущая сессия умерла 24.07 05:04:05
с exit=-1 (4294967295) после ~30.5 ч, новая (PID 18784) поднялась, но подтверждения, что
мост реально встал и устройство «turbobaby-pc» видно сервису, НЕТ НИ В ОДНОМ логе —
ливнесс-пробы в rc_supervisor нет (известный корень, артефакт 915021a). Сейчас у 18784
0 TCP-соединений (сам по себе не диагноз — здоровый RC тоже опускается до 0 за ~15 мин).
Сон ПК исключён фактом: аптайм с 20.07 02:30, последний уход в сон 11.07.

Дополнительный подозреваемый: протухший клиент 2.1.217 при сломанном с 22.07
автообновлении (install_failed) — устаревший RC-клиент может подключаться, но не
попадать в обновлённый список устройств.

## Развилка (решение владельца)

1. **Проверить аккаунт в приложении**: список устройств виден только под
   1turbobaby@gmail.com («…'s Organization»). Другой аккаунт/орг в приложении = пустой список.
2. **Рестарт RC-сессии с дебагом**: положить `rc_debug.flag` и перезапустить сессию
   (kill 18784 → супервизор поднимет с `--debug-file`) — в rc_session_debug.log будет видно,
   встал ли мост и что ответил сервис. Убийство процесса — красное, нужно «да».
3. **Починить CLI**: `claude install` (или обновление MSIX) под mxfill1 в видимом окне —
   снимет install_failed и версию 2.1.217, заодно починит .local\bin и where.exe.
