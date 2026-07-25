# Токен GitHub убран из адресов репозиториев VPS

**Дата:** 25.07.2026 15:05 UTC (22:05 Бангкок) · **Бэкап:** `/root/_credfix_20260725`
**Опора:** [2026-07-25-vps-secrets-posture.md](2026-07-25-vps-secrets-posture.md)

---

## 0. Поправка к постановке: репозиториев ЧЕТЫРЕ, а не три

```
/root/baseline-origin-wt                   origin = …@github.com/mxfill77/turbobaby-manager-bot.git
/root/_pcport_userbot_185                  origin = …@github.com/mxfill77/turbobaby-userbot.git
/root/turbobaby-manager-bot                origin = …@github.com/mxfill77/turbobaby-manager-bot.git
/root/turbobaby-manager-bot/_pcport185     origin = …@github.com/mxfill77/turbobaby-userbot.git
```

Токен был зашит в адрес **каждого** из четырёх. Два последних — копии ПК-репозитория на сервере,
один лежит ВНУТРИ боевого дерева (`turbobaby-manager-bot/_pcport185`).

## 1. Причина прошлого провала — найдена

Помощник учётных данных **был настроен правильно** ещё тогда:

```
credential.helper (global): [store --file=/root/.git-credentials]
```

Ломался не помощник, а **формат файла**:

```
файл есть: права=600 владелец=root строк=1
строка 1: протокол=https хост=github.com логин=НЕТ пароль=НЕТ (длина учётной части=40)
```

В файле лежал **голый токен без логина**. Формат `git credential-store` требует
`https://<логин>:<пароль>@<хост>` — без разделителя `:` git не может отдать пару «логин+пароль»,
и GitHub отвечает отказом. Воспроизведено дословно по каждому репозиторию (чистый адрес +
то самое хранилище):

```
[/root/baseline-origin-wt]
  адрес: https://github.com/mxfill77/turbobaby-manager-bot.git
  remote: Repository not found.
  fatal: Authentication failed for 'https://github.com/mxfill77/turbobaby-manager-bot.git/'

[/root/_pcport_userbot_185]
  адрес: https://github.com/mxfill77/turbobaby-userbot.git
  remote: Repository not found.
  fatal: Authentication failed for 'https://github.com/mxfill77/turbobaby-userbot.git/'

[/root/turbobaby-manager-bot]
  адрес: https://github.com/mxfill77/turbobaby-manager-bot.git
  remote: Repository not found.
  fatal: Authentication failed for 'https://github.com/mxfill77/turbobaby-manager-bot.git/'

[/root/turbobaby-manager-bot/_pcport185]
  адрес: https://github.com/mxfill77/turbobaby-userbot.git
  remote: Repository not found.
  fatal: Authentication failed for 'https://github.com/mxfill77/turbobaby-userbot.git/'
```

⚠️ **`Repository not found` здесь не значит «репозитория нет».** Это то, как GitHub маскирует
отказ авторизации на приватном репозитории: без валидных данных он не подтверждает даже сам факт
существования. Именно это сообщение и увело прошлую попытку в сторону — выглядит как проблема
адреса, а на деле проблема учётных данных.

## 2. Выбранный способ и почему

```
SSH-проба: git@github.com: Permission denied (publickey).
```

**Способ: `credential-store`, файл 600 у root** — единственный помощник, который работает без
терминала и переживает перезагрузку: `cache` умирает по таймауту, `libsecret` требует сессии
рабочего стола, а SSH-ключ на GitHub **не авторизован** (проба выше), поэтому перевод на `git@`
сегодня невозможен.

**Что это даёт честно:** токен перестаёт быть частью адреса — он больше не попадает в
`git remote -v`, `.git/config`, аргументы процессов, логи и артефакты. Это **локализация** секрета
в одном файле с правами 600, а не шифрование. Файл по-прежнему читается пользователем root, но
root на этой машине и так владеет всем.

## 3. Применение

Учётная часть разобрана из адреса: логин отсутствовал, поэтому подставлен канонический
`x-access-token`, пароль — прежний токен (длина 40, значение не печаталось нигде). Записан файл
`/root/.git-credentials` в правильном формате, права 600.

**Проба сделана ДО правки адресов** — сначала убедились, что хранилище отвечает, и только потом
трогали remote:

```
--- ПРОБА хранилища ДО правки адресов (чистый адрес, без терминала):
    <40HEX>	HEAD
    <40HEX>	refs/heads/main
    <40HEX>	refs/heads/rebuild-main
    exit=0
    хранилище отвечает → чищу адреса
```

Если бы проба упала, адреса остались бы нетронутыми, а хранилище откатилось из бэкапа.

## 4. Проверка фактом

```
[/root/baseline-origin-wt]
  адрес ДОСЛОВНО: https://github.com/mxfill77/turbobaby-manager-bot.git   → токена нет
  ls-remote exit=0
[/root/_pcport_userbot_185]
  адрес ДОСЛОВНО: https://github.com/mxfill77/turbobaby-userbot.git       → токена нет
  ls-remote exit=0
[/root/turbobaby-manager-bot]
  адрес ДОСЛОВНО: https://github.com/mxfill77/turbobaby-manager-bot.git   → токена нет
  ls-remote exit=0
[/root/turbobaby-manager-bot/_pcport185]
  адрес ДОСЛОВНО: https://github.com/mxfill77/turbobaby-userbot.git       → токена нет
  ls-remote exit=0
```

Остатков секрета в конфигах git не осталось:

```
/root/.gitconfig                                    совпадений: 0
/root/_pcport_userbot_185/.git/config               совпадений: 0
/root/turbobaby-manager-bot/.git/config             совпадений: 0
/root/turbobaby-manager-bot/_pcport185/.git/config  совпадений: 0
```

## 5. Живая проверка: авто-фетч

Прогон без терминала (`setsid`, `GIT_TERMINAL_PROMPT=0`, stdin из `/dev/null`) — те же условия,
в которых фетчит демон:

```
[/root/baseline-origin-wt] fetch exit=0                    ahead/behind: 23  0
[/root/_pcport_userbot_185] fetch exit=0                   ahead/behind: 191 0
   From https://github.com/mxfill77/turbobaby-userbot
      f7414dd..ec6d8ee  main -> origin/main
[/root/turbobaby-manager-bot] fetch exit=0                 ahead/behind: 0   0
[/root/turbobaby-manager-bot/_pcport185] fetch exit=0      ahead/behind: 192 1
   From https://github.com/mxfill77/turbobaby-userbot
      2d58e9f..ec6d8ee  main -> origin/main
```

Две копии реально **подтянули новые коммиты** (сегодняшний `ec6d8ee` с ПК) — то есть фетч не
просто «не упал», а сделал работу.

## 6. Мелочь, которую стоит знать

Скрипт отчитался об очистке **трёх** адресов, а чистыми оказались **четыре**. Объяснение,
согласующееся с фактами: `/root/baseline-origin-wt` — рабочее дерево (worktree) основного
репозитория, и `.git/config` у них общий. Как только адрес почистили в дереве, у
`turbobaby-manager-bot` он тоже стал чистым, и до него цикл дошёл уже с нечего-делать. Это
предположение, а не измеренный факт: отдельной проверкой `git worktree list` не подтверждал.

## Остатки (решение владельца)

1. **Три лишних клона на сервере.** `_pcport_userbot_185` (191 коммит позади),
   `turbobaby-manager-bot/_pcport185` (192 позади, 1 впереди — там есть НЕотгруженная работа),
   `baseline-origin-wt` (23 позади). Первый и второй — копии ПК-репозитория, живут на сервере без
   видимой нужды; второй лежит внутри боевого дерева. Прежде чем убирать — разобрать тот
   1 коммит впереди.
2. **SSH-ключ к GitHub не авторизован.** Если завести deploy-key, токен можно убрать совсем,
   а не только из адресов. Это следующий шаг того же направления.
3. Токен по-прежнему лежит открытым текстом в `/root/.git-credentials` (600). Настоящее
   шифрование на headless-сервере потребовало бы внешнего хранилища секретов.
