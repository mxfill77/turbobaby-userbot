# -*- coding: utf-8 -*-
"""
brain_writer.py — ДОВЕРЕННЫЙ ПИСАТЕЛЬ в мозг (Brain-доки) с ПК. Образец — VPS-cclog.

ЗАЧЕМ (класс 328, «утренний ASK про пульс»). Одноразовые скрипты записи в мозг сами читали
конфиг с секретами Bridge — а это доктринально КРАСНОЕ по гарду ПК: каждая дозапись строки в
док превращалась в ASK владельцу. Разрыв закрывается разделением ролей: секреты Bridge берёт
ТОЛЬКО этот модуль (сам процесс, из окружения или конфига репо), а агентские скрипты секретов
не читают и в коде не видят — они зовут функцию/CLI писателя. Гард держит канал зелёным ПО
ИМЕНИ модуля (_RE_SAFE_SCRIPTS в pretool_guard), содержимое не сканируется — ровно как у
cowork_log_append/dispatch_notify.

ИНТЕРФЕЙС — «дописать в док по file id (или имени манифеста)»:
  функция  brain_writer.append(text, doc_id=… | name=…, [anchor=…, place=…, require_above=…])
  движок   brain_writer.apply(mutate, …) — для одноразовых скриптов со СВОЕЙ якорной логикой
           (mutate: старый текст → новый | None=no-op; сам скрипт секретов не касается)
  чтение   brain_writer.read_text(doc_id=… | name=…)
  CLI      venv/Scripts/python.exe brain_writer.py (--id FILE_ID | --name ДОК) [ключи] "текст"
           текст «-» или пустой → читается из stdin (многострочные блоки)
           --probe → только чтение: длина и голова дока, ничего не пишем

FAIL-SAFE ВСЕЙ ЗАПИСИ (как у kb_master_append_2307, теперь встроено в канал):
  • адресация: РОВНО один из doc_id/name; Bridge принимает и id=, и name= (проба 23.07,
    см. trainer_log) — но создавать доки канал НЕ умеет, только перезаписывать существующие;
  • якорная вставка: --anchor обязан найтись РОВНО один раз (re.MULTILINE), --require-above
    обязан стоять ВЫШЕ точки вставки; не сошлось → НЕ пишем (остановка лучше записи мимо);
  • идемпотентность: дописываемый текст уже в доке → no-op, повтор ничего не задваивает;
  • бэкап: старый текст целиком ложится в tmp/brain_backup_<тег>_<штамп>.txt ДО записи
    (откат = запись этим текстом);
  • ГАРД УСЫХАНИЯ (класс 17.07.2026, образец VPS cclog.py:200-203): пустой текст от моста —
    это ОТКАЗ ЧТЕНИЯ, а не пустой док (тот же {"ok":true,"text":""} приходит и при сорванном
    чтении), а новый текст короче старого не пишется вовсе; осознанное сокращение объявляется
    явно — apply(…, allow_shrink=True);
  • ОБРАТНОЕ ЧТЕНИЕ: после записи док читается заново, текст сверяется ДОСЛОВНО, маркер
    считается (ровно 1), СВЕРЯЕТСЯ ДЛИНА (наличие строки не отличает «легло целиком» от
    «легло вместо дока»), возвращается фрагмент вокруг вставки — FACT, не «наверное»;
  • гард тестового контекста: под гейтом/юнитами (log_setup.is_test_context) живой док не
    трогаем, если транспорт не инжектирован мок-тестом — урок инцидента KB_trainer_log 23.07
    (гейт залил 30 фикстурных строк в живой док).

Секреты не печатаем и наружу не отдаём; в исключениях и выводе — только имена/длины.
"""
import os
import re
import sys
import json
import datetime
import urllib.request
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
BACKUP_DIR = os.path.join(BASE_DIR, "tmp")
HTTP_TIMEOUT = 30
# Запас на сверке длины обратного чтения: Docs при clear()+setText() нормализуют хвостовой
# перевод строки, поэтому строгое равенство давало бы ложный отказ на ±1 символ. Усечение
# (ради которого сверка и заведена) на порядки крупнее этого запаса.
_READBACK_SLACK = 2


class BrainWriterError(RuntimeError):
    """Отказ канала с честной причиной; .code — код выхода CLI (1 конфиг/аргументы,
    2 чтение, 3 якорь, 4 запись, 5 верификация обратным чтением, 6 гард усыхания)."""
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


# ------------------------------- конфиг (секреты) ----------------------------

def _env_file(path=None):
    """Конфиг репо построчно (K=V, как cowork_log_append/trainer_log). Ошибок не бросает."""
    vals = {}
    try:
        with open(path or ENV_PATH, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return vals


def _config(env=None):
    """BRIDGE_URL/BRIDGE_TOKEN: окружение процесса → конфиг репо. env — инъекция тестов."""
    if env is not None:
        return env.get("BRIDGE_URL"), env.get("BRIDGE_TOKEN")
    vals = _env_file()
    url = (os.environ.get("BRIDGE_URL") or vals.get("BRIDGE_URL") or "").strip()
    token = (os.environ.get("BRIDGE_TOKEN") or vals.get("BRIDGE_TOKEN") or "").strip()
    return url, token


def _in_test_context():
    """Идёт тестовый прогон? Точка правды репо — log_setup.is_test_context (как trainer_log)."""
    try:
        from log_setup import is_test_context
        return bool(is_test_context())
    except Exception:
        return False


# ------------------------------- транспорт Bridge ----------------------------

def _get(url, params):
    # read_doc живёт в doGet Bridge → GET, токен в query, в логи/вывод не попадает
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, method="GET")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _doc_text(obj):
    for key in ("text", "content", "fileContent", "body"):
        if isinstance(obj, dict) and isinstance(obj.get(key), str):
            return obj[key]
    return None


def _ref(doc_id, name):
    """→ (params-часть адресации, человекочитаемый ref). РОВНО один из doc_id/name."""
    doc_id, name = (doc_id or "").strip(), (name or "").strip()
    if bool(doc_id) == bool(name):
        raise BrainWriterError("нужен РОВНО один адрес дока: doc_id (--id) ЛИБО name (--name)", 1)
    return ({"id": doc_id}, "id:" + doc_id) if doc_id else ({"name": name}, "name:" + name)


def _read(url, token, addr, ref, get):
    try:
        r = (get or _get)(url, dict(addr, action="read_doc", token=token))
    except Exception as e:
        raise BrainWriterError("read_doc(%s) упал: %s: %s" % (ref, type(e).__name__, e), 2)
    if not (isinstance(r, dict) and r.get("ok")):
        raise BrainWriterError("read_doc(%s) не ok: %s"
                               % (ref, json.dumps(r, ensure_ascii=False)[:300]), 2)
    text = _doc_text(r)
    if text is None:
        raise BrainWriterError("read_doc(%s) ok, но текста нет — НЕ пишу, чтобы не затереть" % ref, 2)
    # Пустой текст = ОТКАЗ ЧТЕНИЯ, а не пустой док: тот же ответ {"ok":true,"text":""} мост отдаёт
    # и при сорванном чтении, а канал доки не создаёт — только перезаписывает существующие.
    # Проверка стоит ЗДЕСЬ, поэтому закрывает и первое чтение, и обратное.
    if not text.strip():
        raise BrainWriterError("read_doc(%s) вернул ПУСТОЙ текст (%d символов) — считаю это ОТКАЗОМ "
                               "ЧТЕНИЯ, а не пустым доком: писать поверх значит затереть"
                               % (ref, len(text)), 2)
    return text


def read_text(doc_id="", name="", env=None, get=None):
    """Текст дока (read-only) или BrainWriterError. Секретов не раскрывает."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    addr, ref = _ref(doc_id, name)
    return _read(url, token, addr, ref, get)


def create_plain(name, key="", text="", env=None, post=None):
    """Создать НОВЫЙ plain-док в папке Brain (Bridge-экшен create_brain_plain) и, если задан key,
    зарегистрировать его в манифесте — после этого док адресуется по имени, как остальные.

    Зачем отдельной функцией: канал дозаписи (append/apply) доки СОЗДАВАТЬ не умеет, только
    перезаписывать существующие. А создавать их скриптом-однодневкой нельзя — он читал бы
    BRIDGE_TOKEN сам (запрет класса 328). Значит создание живёт здесь, у доверенного писателя:
    секреты берёт этот модуль, вызывающий их не видит. → dict ответа Bridge (ok, id, …)."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    if not (name or "").strip():
        raise BrainWriterError("нужно имя нового дока", 1)
    if post is None and _in_test_context():
        raise BrainWriterError("тестовый контекст (гейт/юнит): живой Brain НЕ трогаем — "
                               "инжектируй post мок-тестом", 1)
    payload = {"action": "create_brain_plain", "token": token, "name": name, "text": text}
    if key:
        payload["key"] = key
    try:
        r = (post or _post)(url, payload)
    except Exception as e:
        raise BrainWriterError("create_brain_plain(%s) упал: %s: %s"
                               % (name, type(e).__name__, e), 4)
    if not (isinstance(r, dict) and r.get("ok")):
        raise BrainWriterError("create_brain_plain(%s) не ok: %s"
                               % (name, json.dumps(r, ensure_ascii=False)[:300]), 4)
    return r


# ------------------------------- запись (движок) -----------------------------

def _backup(old, tag, backup_dir=None):
    d = backup_dir or BACKUP_DIR
    os.makedirs(d, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w.-]+", "_", tag or "doc") or "doc"
    path = os.path.join(d, "brain_backup_%s_%s.txt" % (safe, stamp))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(old)
    return path


def apply(mutate, doc_id="", name="", expect=None, marker=None,
          backup_tag="", backup_dir=None, env=None, get=None, post=None, allow_shrink=False):
    """Движок записи: read → mutate(старый текст) → бэкап → write → ОБРАТНОЕ ЧТЕНИЕ.

    mutate: str → str | None (None = no-op, ничего не пишем); ValueError из mutate = «якорь
    не прошёл» — док НЕ тронут. expect — строка, обязанная лечь в док ДОСЛОВНО (сверка по
    живому доку после записи); marker — строка-маркер, обязанная встретиться РОВНО 1 раз.
    allow_shrink — снять ГАРД УСЫХАНИЯ (по умолчанию новый текст короче старого = отказ, код 6).
    Объявляется ЯВНО одноразовыми скриптами, которые режут док осознанно; молчаливое
    укорачивание журнала запрещено — это и есть класс 17.07.
    → dict: status ok|noop, doc, backup, back (текст обратного чтения), before_chars,
    after_chars, fragment.
    Любой отказ — BrainWriterError с честной причиной (см. коды в классе)."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    addr, ref = _ref(doc_id, name)
    if (get is None or post is None) and _in_test_context():
        raise BrainWriterError("тестовый контекст (гейт/юнит): живой док %s НЕ трогаем — "
                               "инжектируй get/post мок-тестом" % ref, 1)
    old = _read(url, token, addr, ref, get)
    try:
        new = mutate(old)
    except ValueError as e:
        raise BrainWriterError("ЯКОРЬ НЕ ПРОШЁЛ (%s): %s — док НЕ тронут" % (ref, e), 3)
    if new is None or new == old:
        return {"status": "noop", "doc": ref, "backup": None, "back": old,
                "before_chars": len(old), "after_chars": len(old), "fragment": ""}
    # ГАРД УСЫХАНИЯ (класс 17.07, образец cclog.py:200-203): молча укоротить док нельзя.
    if not allow_shrink and len(new) < len(old):
        raise BrainWriterError("ГАРД УСЫХАНИЯ (%s): новый текст короче старого (было %d символов, "
                               "стало бы %d) — НЕ пишу; осознанное сокращение объявляется "
                               "allow_shrink=True" % (ref, len(old), len(new)), 6)
    backup = _backup(old, backup_tag or (name or doc_id), backup_dir)
    try:
        w = (post or _post)(url, dict(addr, action="write_doc", token=token, text=new))
    except Exception as e:
        raise BrainWriterError("write_doc(%s) упал: %s: %s (бэкап: %s)"
                               % (ref, type(e).__name__, e, backup), 4)
    if not (isinstance(w, dict) and w.get("ok")):
        raise BrainWriterError("write_doc(%s) не ok: %s (бэкап: %s)"
                               % (ref, json.dumps(w, ensure_ascii=False)[:300], backup), 4)
    # ОБРАТНОЕ ЧТЕНИЕ — верификация по живому доку, не по локальной склейке
    back = _read(url, token, addr, ref, get)
    # СВЕРКА ДЛИНЫ. Проверка «expect есть в тексте» НЕ отличает «легло целиком» от «легло ВМЕСТО
    # дока»: усечённый док тоже содержит новую строку. Сравниваем с длиной ДО записи и с длиной
    # отправленного (последнее — с запасом _READBACK_SLACK на нормализацию хвоста в Docs).
    if not allow_shrink and len(back) < len(old):
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): док УКОРОТИЛСЯ — было %d символов, стало %d "
                               "(бэкап: %s)" % (ref, len(old), len(back), backup), 5)
    if len(back) < len(new) - _READBACK_SLACK:
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): отправляли %d символов, в доке %d — запись "
                               "легла НЕ ЦЕЛИКОМ (бэкап: %s)"
                               % (ref, len(new), len(back), backup), 5)
    if expect is not None and expect not in back:
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): текст в доке НЕ найден дословно — "
                               "разберись перед повтором (бэкап: %s)" % (ref, backup), 5)
    if marker is not None and back.count(marker) != 1:
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): маркер встречается %d раз, ожидал 1 "
                               "(бэкап: %s)" % (ref, back.count(marker), backup), 5)
    probe = marker if marker is not None else expect
    fragment = ""
    if probe:
        i = back.find(probe)
        fragment = back[max(0, i - 200):i + len(expect or probe) + 120]
    return {"status": "ok", "doc": ref, "backup": backup, "back": back,
            "before_chars": len(old), "after_chars": len(back), "fragment": fragment}


# ------------------------------- дозапись (интерфейс) ------------------------

def _whole_line_count(body, text):
    """Сколько раз body стоит в text ЦЕЛЬНЫМИ строками (границы — край текста или «\\n»).
    Подстрока внутри чужой строки НЕ считается: «PASS» внутри «PASS 2/2» — не вхождение.
    Иначе идемпотентность давала бы ложный no-op (короткая строка «уже есть» в чужой длинной =
    молча потерянная запись), а сверка — ложный провал на легальном повторе подстроки."""
    n, i = 0, text.find(body)
    while i != -1:
        j = i + len(body)
        if (i == 0 or text[i - 1] == "\n") and (j == len(text) or text[j] == "\n"):
            n += 1
        i = text.find(body, i + 1)
    return n


def append(text, doc_id="", name="", anchor=None, place=None, require_above=None,
           backup_tag="", backup_dir=None, env=None, get=None, post=None):
    """ДОЗАПИСАТЬ текст в док по file id (или имени манифеста). → результат apply().

    Без anchor: place «top» (дефолт, новое сверху — стиль cowork_log) или «bottom».
    С anchor (regex, re.MULTILINE, обязан найтись ровно 1 раз): place «before» (дефолт) или
    «after» — вставка отдельным абзацем (пустая строка до и после). require_above — подстрока,
    обязанная существовать ВЫШЕ точки вставки (страховка структуры дока). Идемпотентность и
    обратная сверка считают текст ЦЕЛЬНЫМИ строками (_whole_line_count)."""
    body = (text or "").rstrip("\n")
    if not body.strip():
        raise BrainWriterError("пустой текст — дописывать нечего", 1)
    place = place or ("before" if anchor else "top")
    if anchor and place not in ("before", "after"):
        raise BrainWriterError("с anchor допустимы place=before|after, а не %r" % place, 1)
    if not anchor and place not in ("top", "bottom"):
        raise BrainWriterError("без anchor допустимы place=top|bottom, а не %r" % place, 1)

    def mutate(old):
        if _whole_line_count(body, old):
            return None                      # идемпотентность: уже стоит цельными строками → no-op
        if not anchor:
            if place == "top":
                return body + "\n" + old
            return old + ("" if old.endswith("\n") else "\n") + body
        hits = list(re.finditer(anchor, old, re.MULTILINE))
        if len(hits) != 1:
            raise ValueError("якорь %r найден %d раз (ожидал 1)" % (anchor, len(hits)))
        pos = hits[0].start() if place == "before" else hits[0].end()
        if require_above and require_above not in old[:pos]:
            raise ValueError("выше якоря нет обязательной подстроки %r — вставлять некуда"
                             % require_above)
        if place == "before":
            return old[:pos].rstrip("\n") + "\n\n" + body + "\n\n" + old[pos:]
        return old[:pos] + "\n\n" + body + "\n\n" + old[pos:].lstrip("\n")

    res = apply(mutate, doc_id=doc_id, name=name, expect=body, marker=None,
                backup_tag=backup_tag, backup_dir=backup_dir, env=env, get=get, post=post)
    if res["status"] == "ok":
        cnt = _whole_line_count(body, res["back"])
        if cnt != 1:
            raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): текст лёг %d раз(а) цельными "
                                   "строками, ожидал 1 (бэкап: %s)"
                                   % (res["doc"], cnt, res["backup"]), 5)
    return res


# ------------------------------- CLI -----------------------------------------

def _out(s):
    sys.stdout.buffer.write((s + "\n").encode("utf-8"))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="brain_writer",
        description="Доверенный писатель в Brain-док: дозапись по file id/имени с бэкапом, "
                    "якорной вставкой и обратным чтением. Секреты Bridge читает сам.")
    ap.add_argument("--id", dest="doc_id", default="", help="file id дока (Drive)")
    ap.add_argument("--name", default="", help="имя дока в манифесте Bridge (index, cowork_log, …)")
    ap.add_argument("--probe", action="store_true", help="только чтение: длина и голова дока")
    ap.add_argument("--anchor", default=None, help="regex якоря (re.MULTILINE), ровно 1 совпадение")
    ap.add_argument("--place", default=None, choices=("top", "bottom", "before", "after"))
    ap.add_argument("--require-above", dest="require_above", default=None,
                    help="подстрока, обязанная стоять выше точки вставки")
    ap.add_argument("--backup-tag", dest="backup_tag", default="", help="тег имени файла бэкапа")
    ap.add_argument("text", nargs="*", help="текст дозаписи; «-» или пусто → из stdin")
    a = ap.parse_args(argv)
    try:
        if a.probe:
            t = read_text(doc_id=a.doc_id, name=a.name)
            _out("PROBE ok: %d символов, голова:" % len(t))
            _out(t[:600])
            return 0
        text = " ".join(a.text).strip()
        if not text or text == "-":
            text = sys.stdin.read()
        res = append(text, doc_id=a.doc_id, name=a.name, anchor=a.anchor, place=a.place,
                     require_above=a.require_above, backup_tag=a.backup_tag)
    except BrainWriterError as e:
        sys.stderr.buffer.write(("ОШИБКА: %s\nНЕ ЗАПИСАНО.\n" % e).encode("utf-8"))
        return e.code
    if res["status"] == "noop":
        _out("уже в доке (%s) — no-op, идемпотентность (док %d символов)"
             % (res["doc"], res["after_chars"]))
        return 0
    _out("записано в %s: %d → %d символов; бэкап: %s"
         % (res["doc"], res["before_chars"], res["after_chars"], res["backup"]))
    _out("ОБРАТНОЕ ЧТЕНИЕ OK: текст дословно на месте (вхождение 1 раз)")
    _out("--- фрагмент вокруг вставки ---")
    _out(res["fragment"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
