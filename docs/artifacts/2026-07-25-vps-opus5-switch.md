# Полоса стройки VPS → Opus 5 (25.07.2026, применено)

Клиентский `SUGGEST_MODEL` НЕ тронут. Секреты не выводились.

## 1. Справка CLI (claude 2.1.215, `/usr/bin/claude`) — дословно

```
  --effort <level>                      Effort level for the current session
                                        (low, medium, high, xhigh, max)
  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name (e.g.
                                        'claude-fable-5').
```

**Списка доступных моделей в `claude --help` НЕТ** — только формат (алиас либо полное имя);
подкоманды `models` не существует. Поэтому идентификатор проверяется пробой, а не справкой.

## 2. Идентификатор Opus 5 — установлен ФАКТОМ

Метод: `claude -p --output-format json --model <id> --effort xhigh` + **контроль заведомо
несуществующей моделью** (без контроля проба ничего не доказывает).

| проба | результат |
|---|---|
| `claude-opus-99-nonexistent-zzz` (контроль) | `exit=1`, `is_error:true`, **`api_error_status:404`**, «It may not exist or you may not have access to it» ⇒ метод рабочий, API реально отвергает несуществующие |
| **`claude-opus-5`** | `exit=0`, `is_error:false`, и учёт сервера: **`"modelUsage":{"claude-opus-5":{…}}`** ⇒ **модель существует и отработала** |
| алиас `opus` | отвечает **`claude-opus-4-8`** (+`claude-haiku-4-5-20251001` как вспомогательная) |

⚠️ **Ключевое: алиас `opus` ОТСТАЁТ — он указывает на `claude-opus-4-8`, а не на Opus 5.**
Чтобы реально получить Opus 5, нужен **полный id `claude-opus-5`**; алиас молча оставил бы
полосу на прошлом поколении.

## 3-4. Применено и перезапущено

`.env` (бэкап `.env.bak-opus5-*` создан до правки):
```
14:ORCH_MODEL=claude-opus-5
15:ORCH_MODEL_FALLBACK=fable
19:EXECUTOR_MODEL=claude-opus-5
67:THINKER_MODEL=claude-opus-5
```
`SUGGEST_MODEL` — ключа в `.env` нет вовсе (прайс-бот берёт свой дефолт в `suggest.py`), **не тронут**.
`ORCH_MODEL_FALLBACK=fable` — прежняя рабочая `ORCH_MODEL` (была `fable`), как и просили: при сбое
новой полоса не встанет.

Демон перезапущен штатно (`systemctl restart orchestrator-daemon`), баннер старта:
```
=== ДЕМОН СТАРТ (… claude=/usr/bin/claude, … model=claude-opus-5, executor_model=claude-opus-5,
    mem_gate=on(min=2560MB avail=7101MB), rss_gate=on, proc_gate=on …) ===
```

## 5. Реальная read-only задача — METRICS ДОСЛОВНО

Задача 398 поставлена штатно через `BridgeClient.enqueue_task`:
```
2026-07-24 18:35:24,864 INFO METRICS task=398 lane=vps model=claude-haiku-4-5-20251001,claude-opus-5 effort=xhigh start=2026-07-24T18:35:13+00:00 end=2026-07-24T18:35:24+00:00 dur_s=11 outcome=done attempts=1 selfheals=0 tokens_in=118218 tokens_out=518
```
```
2026-07-24 18:35:24,863 INFO id=398 модель отработала: claude-haiku-4-5-20251001,claude-opus-5 (запрошена=claude-opus-5, фолбэк=fable, взята=основная)
2026-07-24 18:35:24,864 INFO id=398 claude -p exit=0 (вывод 183 симв)
2026-07-24 18:35:27,220 INFO COMPLETE id=398 status=done bridge_ok=True
```

## 6. Прямой ответ

**Полоса на Opus 5** — `запрошена=claude-opus-5 … взята=основная` (фолбэк НЕ сработал).
**Усилия xhigh** — `effort=xhigh`. **Длительность реальная** — `dur_s=11` (не 0, как в прежних
замерах 24.07: `dur_s=0`). `haiku-4-5` в строке — служебная вспомогательная модель CLI, не исполнитель.

## Грабли на будущее (стоили двух прогонов)

1. **`claude -p` внутри `ssh 'bash -s' <<EOF` съедает остаток heredoc'а**, потому что наследует
   stdin скрипта — скрипт молча обрывается на первом же вызове claude. Лечится `< /dev/null` у
   КАЖДОГО вызова claude (и вообще у любой команды, читающей stdin).
2. **Проба существования модели без контроля бессмысленна**: сначала убедись, что заведомо
   фейковый id даёт 404, и только потом верь успеху целевого.
3. `BridgeClient` сам `.env` НЕ грузит — нужен `load_dotenv()`; скрипт из `/tmp` не видит модулей
   репо (нужен `PYTHONPATH`/запуск из каталога репо).
