# -*- coding: utf-8 -*-
"""
test_chatlog.py — регресс хранилища рабочей переписки (первая очередь, 05.09.2026).

ЧЕГО ЭТИ ТЕСТЫ НЕ КАСАЮТСЯ: боевого хранилища, боевых журналов, `moderation_ipc.db` и вообще
любого живого файла состояния. Хранилище уводится во временный каталог ручкой `CHATLOG_ROOT`,
источники захвата подменяются фикстурами через `chatlog_ingest.HERE`. Ни один тест не открывает
`userbot.log`, `dispatch_notify.log` и `data_export/` — иначе зелёный прогон зависел бы от того,
что в эту минуту написал живой процесс.

ЧТО ЗДЕСЬ ДЕРЖИТСЯ РЕГРЕССОМ, а не обещанием в комментарии:
  * ротации нет ни в одной ветке (`TestNoRotation`) — это единственная причина, по которой
    хранилище вообще заведено, и потерять её молча было бы хуже, чем не заводить;
  * корень вне git (`TestOutsideGit`) — исчезнувшая строка `.gitignore` неотличима от целой
    ровно до дня, когда живая переписка уезжает в публичную историю;
  * имени, ника и телефона нет ни в одном поле (`TestNoPerson`);
  * повторный захват безвреден (`TestDedup`);
  * разбор источников повторяет ЖИВОЙ формат (`TestParsers`) — правило полосы «мок обязан
    копировать живой формат»: строки фикстур сняты с боевых писателей, а не придуманы.
"""

import io
import os
import re
import json
import shutil
import tempfile
import unittest

os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")

import chatlog_store as store
import chatlog_ingest as ingest
import chatlog_find as find

HERE = os.path.dirname(os.path.abspath(__file__))


class _Rooted(unittest.TestCase):
    """Каждому тесту — свой пустой корень во временном каталоге."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="turbobaby_TESTING_chatlog_")
        self._save_root = os.environ.get(store.ROOT_ENV)
        os.environ[store.ROOT_ENV] = self.tmp
        store._SALT_CACHE.pop(self.tmp, None)

    def tearDown(self):
        store._SALT_CACHE.pop(self.tmp, None)
        if self._save_root is None:
            os.environ.pop(store.ROOT_ENV, None)
        else:
            os.environ[store.ROOT_ENV] = self._save_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rec(self, ts, who, text, mid=None, topic=None):
        return store.line(ts=ts, ref=store.who_ref(who), text=text, mid=mid,
                          topic_id=topic, topic_name=(str(topic) if topic else None))


# ─────────────────────────── 1. ротации нет ───────────────────────────

class TestNoRotation(unittest.TestCase):
    def setUp(self):
        with io.open(os.path.join(HERE, "chatlog_store.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_no_log_setup_import(self):
        """`log_setup` — общая РОТАЦИЯ контура; один его вызов вернул бы сюда потолок 5 МБ × 4,
        ради ухода от которого хранилище и заведено."""
        self.assertNotIn("import log_setup", self.src)
        self.assertNotIn("RotatingFileHandler", self.src)

    def test_no_backup_shuffling(self):
        """Ни одной ветки, сдвигающей `.1 → .2` или считающей потолок."""
        for bad in ("maxBytes", "backupCount", "rotate_if_needed", "MAX_BYTES", "LOG_BACKUPS"):
            self.assertNotIn(bad, self.src, "в хранилище появилась ротация: %s" % bad)

    def test_only_manifest_opens_for_write(self):
        """Единственный `"w"` во всём модуле — временный файл манифеста. Данные пишутся только
        дописыванием: затирание — это та же потеря, просто под другим именем."""
        opens = re.findall(r'io\.open\([^\n]*?,\s*"([axw])"', self.src)
        self.assertEqual(sorted(opens), ["a", "a", "w", "x"], "режимы открытия: %r" % opens)
        w_lines = [ln for ln in self.src.splitlines() if 'io.open(' in ln and '"w"' in ln]
        self.assertEqual(len(w_lines), 1)
        self.assertIn("tmp", w_lines[0])


class TestNoRotationLive(_Rooted):
    def test_second_write_keeps_first(self):
        """Функциональное доказательство: второй захват в тот же день НЕ трогает первый."""
        w = store.Writer()
        w.add("g", self._rec("2026-09-05T10:00:00", "@a", "первое сообщение"))
        w.commit()
        w2 = store.Writer()
        w2.add("g", self._rec("2026-09-05T11:00:00", "@b", "второе сообщение"))
        st = w2.commit()
        self.assertEqual(st["written"], 1)
        with io.open(store.day_path("g", "2026-09-05"), encoding="utf-8") as f:
            body = f.read()
        self.assertIn("первое сообщение", body)
        self.assertIn("второе сообщение", body)


# ─────────────────────────── 2. вне git ───────────────────────────

class TestOutsideGit(unittest.TestCase):
    def test_gitignore_holds_chatlog(self):
        with io.open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        self.assertIn("chatlog/", lines,
                      "корень хранилища выпал из .gitignore — переписка уедет в git-историю")

    def test_default_root_is_repo_chatlog(self):
        save = os.environ.pop(store.ROOT_ENV, None)
        try:
            self.assertEqual(os.path.basename(store.root()), "chatlog")
        finally:
            if save is not None:
                os.environ[store.ROOT_ENV] = save


# ─────────────────────────── 3. людей в архиве нет ───────────────────────────

class TestNoPerson(_Rooted):
    def test_line_has_no_person_fields(self):
        r = store.line(ts="2026-09-05T10:00:00", ref=store.who_ref("@ivanov"), text="привет")
        for bad in ("name", "username", "nick", "phone", "first_name", "last_name", "author"):
            self.assertNotIn(bad, r, "в строке архива завелось поле человека: %s" % bad)
        self.assertEqual(set(r) - {"k", "mid", "ts", "topic_id", "topic_name", "who_ref",
                                   "text", "media"}, set())

    def test_extra_cannot_overwrite_own_fields(self):
        r = store.line(ts="2026-09-05T10:00:00", ref="pX", text="t",
                       extra={"who_ref": "@настоящий_ник", "text": "подмена"})
        self.assertEqual(r["who_ref"], "pX")
        self.assertEqual(r["text"], "t")

    def test_pseudonym_stable_and_distinct(self):
        a1, a2 = store.who_ref("@ivanov"), store.who_ref("@ivanov")
        b = store.who_ref("@petrov")
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, b)
        self.assertTrue(re.match(r"^p[0-9a-f]{12}$", a1), a1)
        self.assertNotIn("ivanov", a1)

    def test_salt_makes_pseudonym_unguessable(self):
        """Псевдоним без соли развернулся бы перебором по списку user_id. Соль лежит вне git,
        и та же строка в ДРУГОМ хранилище даёт ДРУГОЙ псевдоним."""
        first = store.who_ref("id504608015")
        other = tempfile.mkdtemp(prefix="turbobaby_TESTING_chatlog2_")
        try:
            os.environ[store.ROOT_ENV] = other
            store._SALT_CACHE.pop(other, None)
            self.assertNotEqual(store.who_ref("id504608015"), first)
        finally:
            store._SALT_CACHE.pop(other, None)
            os.environ[store.ROOT_ENV] = self.tmp
            shutil.rmtree(other, ignore_errors=True)

    def test_scrub_masks_nick(self):
        out = store.scrub("спроси @vasya_ivanov про байк")
        self.assertNotIn("vasya_ivanov", out)
        self.assertIn("байк", out)
        self.assertTrue(re.search(r"@p[0-9a-f]{8}", out), out)

    def test_scrub_masks_phone(self):
        for raw in ("+66 81 234 5678", "+7 (912) 345-67-89", "89123456789"):
            out = store.scrub("телефон клиента %s, привезти к 14" % raw)
            self.assertNotIn(raw, out, raw)
            self.assertIn("[tel:", out)
            self.assertIn("привезти", out)

    def test_scrub_keeps_work_numbers(self):
        """Порог в десять цифр разводит телефон и ЧИСЛА рабочей переписки. Съев цену или номер
        байка, маска убила бы смысл архива ради безопасности, которой там нет."""
        for keep in ("цена 4500 бат", "пробег 18730", "дата 2026-09-05", "залог 3000",
                     "нмакс 155 за 12000 в месяц"):
            self.assertEqual(store.scrub(keep), keep, keep)

    def test_scrub_masks_nick_glued_to_word(self):
        """Живой промах аудита 05.09: `[B@rtolu4i](…)` — «собака» после буквы. Пять ников
        уцелели ровно на этом, потому что лукбехайнд исключал `\\w`."""
        out = store.scrub("**От кого: **[B@rtolu4ik]")
        self.assertNotIn("rtolu4ik", out)

    def test_scrub_masks_tme_link(self):
        """Самый крупный носитель ников — 6145 разных ссылок в живом захвате 05.09."""
        out = store.scrub("автор https://t.me/bartolomeo пишет")
        self.assertNotIn("bartolomeo", out)
        self.assertIn("t.me/p", out)
        self.assertIn("пишет", out)

    def test_scrub_keeps_service_tme_paths(self):
        """`t.me/joinchat/…` — служебный путь Telegram, а не имя человека."""
        self.assertIn("t.me/joinchat", store.scrub("вот приглашение t.me/joinchat/AbCdEfGh"))

    def test_tme_and_at_forms_give_same_pseudonym(self):
        """Один человек — один псевдоним, как бы он ни был записан. Иначе поиск по нему
        находил бы половину его следов."""
        a = store.scrub("t.me/bartolomeo").split("/")[-1]
        b = store.scrub("@bartolomeo").lstrip("@")
        self.assertEqual(a, b)

    def test_scrub_masks_email(self):
        out = store.scrub("пиши на ivan.petrov@gmail.com сегодня")
        self.assertNotIn("ivan.petrov", out)
        self.assertNotIn("gmail.com", out)
        self.assertIn("[mail:", out)
        self.assertIn("сегодня", out)

    def test_scrub_keeps_plain_domain(self):
        """Домен компании — не человек: `turbophuket.com` обязан пережить чистку."""
        self.assertEqual(store.scrub("сайт turbophuket.com"), "сайт turbophuket.com")

    def test_scrub_is_idempotent(self):
        for raw in ("пиши @vasya_ivanov", "автор t.me/bartolomeo", "почта a.b@mail.ru",
                    "телефон +66 81 234 5678"):
            once = store.scrub(raw)
            self.assertEqual(store.scrub(once), once, raw)

    def test_key_survives_scrub_change(self):
        """Ключ считается по СЫРОМУ тексту: улучшение чистки не смеет удвоить архив."""
        r1 = store.line(ts="2026-09-05T10:00:00", ref="pX", text="пиши @vasya_ivanov")
        save = store.scrub
        try:
            store.scrub = lambda t: "СОВСЕМ ДРУГОЙ ТЕКСТ"
            r2 = store.line(ts="2026-09-05T10:00:00", ref="pX", text="пиши @vasya_ivanov")
        finally:
            store.scrub = save
        self.assertEqual(r1["k"], r2["k"])

    def test_no_person_field_in_written_file(self):
        w = store.Writer()
        w.add("g", self._rec("2026-09-05T10:00:00", "@ivanov", "звонил @petrov, +66 81 234 5678"))
        w.commit()
        with io.open(store.day_path("g", "2026-09-05"), encoding="utf-8") as f:
            body = f.read()
        for bad in ("ivanov", "petrov", "812345678"):
            self.assertNotIn(bad, body, bad)


# ─────────────────────────── 4. дедуп и индекс ───────────────────────────

class TestDedup(_Rooted):
    def test_repeat_writes_nothing(self):
        def one():
            w = store.Writer()
            w.add("g", self._rec("2026-09-05T10:00:00", "@a", "ровно то же самое", mid=77))
            return w.commit()
        first, second = one(), one()
        self.assertEqual(first["written"], 1)
        self.assertEqual(second["written"], 0)
        self.assertEqual(second["dup"], 1)

    def test_repeat_without_mid_also_deduped(self):
        """У `delivery_6m.jsonl` номеров сообщений нет вовсе — ключ считается по содержимому."""
        def one():
            w = store.Writer()
            w.add("g", self._rec("2026-09-05T10:00:00", "@a", "без номера"))
            return w.commit()
        self.assertEqual(one()["written"], 1)
        self.assertEqual(one()["written"], 0)

    def test_index_row_per_message(self):
        w = store.Writer()
        w.add("g", self._rec("2026-09-05T10:00:00", "@a", "альфа", mid=1, topic=1160))
        w.add("g", self._rec("2026-09-06T10:00:00", "@a", "бета", mid=2, topic=328))
        st = w.commit()
        self.assertEqual(st["index_rows"], 2)
        with io.open(store.index_path("2026-09"), encoding="utf-8") as f:
            rows = [ln.rstrip("\n").split("\t") for ln in f if ln.strip()]
        self.assertEqual([r[0] for r in rows], ["2026-09-05", "2026-09-06"])
        self.assertEqual(rows[0][1], "g")
        self.assertEqual(rows[0][3], "m1")
        self.assertEqual(rows[0][5], "альфа")

    def test_index_not_duplicated_on_repeat(self):
        for _ in range(2):
            w = store.Writer()
            w.add("g", self._rec("2026-09-05T10:00:00", "@a", "альфа", mid=1))
            w.commit()
        with io.open(store.index_path("2026-09"), encoding="utf-8") as f:
            self.assertEqual(len([1 for ln in f if ln.strip()]), 1)

    def test_message_without_day_is_not_lost_silently(self):
        w = store.Writer()
        self.assertFalse(w.add("g", self._rec(None, "@a", "без времени")))
        st = w.commit()
        self.assertEqual(st["skipped_no_day"], 1)

    def test_manifest_written(self):
        w = store.Writer()
        w.note_group("g", title="Группа", group_id=-100500, kind="group", source="проба")
        w.add("g", self._rec("2026-09-05T10:00:00", "@a", "текст"))
        w.commit()
        m = store.read_manifest()
        self.assertEqual(m["groups"]["g"]["group_id"], -100500)
        self.assertEqual(m["groups"]["g"]["first_day"], "2026-09-05")
        self.assertEqual(m["groups"]["g"]["messages"], 1)


# ─────────────────────────── 5. разбор ЖИВЫХ форматов ───────────────────────────

# Строки ниже сняты с боевых писателей, а не придуманы:
#   userbot  — `userbot_listen.py:151` при хендлере fmt="%(message)s" (префикса логгера НЕТ);
#   dispatch — `%(asctime)s | %(message)s` + `итог(...): channel=… ok=… [mid=…] | текст`.
UB_LIVE = "\n".join([
    "2026-09-04T09:12:33+00:00 | @client_one | Иван Петров | добрый день, есть нмакс на неделю?",
    "2026-09-04T09:13:01+00:00 | id704112233 | ? | [без текста / медиа]",
    "2026-09-05T07:00:00+00:00 | @client_two | Anna | нужен байк с доставкой в Раваи",
    # служебная строка ТОГО ЖЕ журнала: начинается тем же ISO и обязана быть отброшена
    "2026-09-05T07:00:05+00:00 | SUGGEST: сбой генерации черновика: ValueError | что-то | ещё",
    "запуск userbot, версия чего-нибудь",
])

DN_LIVE = "\n".join([
    # СТАРАЯ строка — без поля mid=, текст обрезан по 90 символов
    "2026-09-04 11:02:03,123 | итог(критич): channel=inbox ok=True | ⛔ Ворота клиентского",
    # НОВАЯ строка — с mid= и целым текстом
    # текст ДЛИННЕЕ прежнего потолка в 90 символов — иначе «обрезка снята» не доказывается
    "2026-09-05 12:00:00,001 | итог(тема): channel=topic:328 ok=True mid=9812 | "
    "✅ Code-сессия завершена: собрали корень хранилища переписки, месячный индекс и дверь "
    "поиска ⏎ и сняли обрезку исходящих в восьми одинаковых местах одного файла",
    "2026-09-05 12:05:00,002 | итог: channel=DM ok=True mid=451 | 🔔 Dispatch: пинг",
    "2026-09-05 12:06:00,003 | итог(notification): channel=none ok=False mid=- | не ушло",
    "2026-09-05 12:07:00,004 | хук=stop | MAX_THINKING_TOKENS=31999",
])


class TestParsers(_Rooted):
    def setUp(self):
        _Rooted.setUp(self)
        self.src = tempfile.mkdtemp(prefix="turbobaby_TESTING_src_")
        self._save_here = ingest.HERE
        ingest.HERE = self.src

    def tearDown(self):
        ingest.HERE = self._save_here
        shutil.rmtree(self.src, ignore_errors=True)
        _Rooted.tearDown(self)

    def _put(self, name, body):
        p = os.path.join(self.src, name)
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        return p

    def test_userbot_takes_only_correspondence(self):
        self._put("userbot.log", UB_LIVE + "\n")
        w = store.Writer()
        n = ingest.ingest_userbot(w)
        self.assertEqual(n, 3, "служебная строка журнала попала в архив как сообщение")
        w.commit()
        rows = find.search_days([], group=ingest.GROUP_CLIENT_DM)
        self.assertEqual(len(rows), 3)
        body = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("Иван Петров", body, "имя профиля уехало в архив")
        self.assertNotIn("client_one", body, "ник уехал в архив")
        self.assertIn("нмакс", body)

    def test_userbot_reads_rotated_copy_too(self):
        """Смысл всей задачи: `.log.1` — файл, который уходит первым при следующем перекате."""
        self._put("userbot.log", UB_LIVE + "\n")
        self._put("userbot.log.1",
                  "2026-08-01T05:00:00+00:00 | @old_client | Старый | прошлогодний вопрос\n")
        w = store.Writer()
        self.assertEqual(ingest.ingest_userbot(w), 4)
        w.commit()
        self.assertEqual(len(find.search_days(["прошлогодний"])), 1)

    def test_userbot_media_marker(self):
        self._put("userbot.log", UB_LIVE + "\n")
        w = store.Writer()
        ingest.ingest_userbot(w)
        w.commit()
        rows = [r for r in find.search_days([]) if r["media"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["media"][0]["kind"], "unknown")

    def test_dispatch_old_line_marked_cut(self):
        """Отсутствие поля `mid=` — подпись строки, написанной кодом СТАРШЕ 05.09: заведомо
        обрезанной. Наличие `mid=-` означает другое: текст целый, номера нет."""
        self._put("dispatch_notify.log", DN_LIVE + "\n")
        w = store.Writer()
        n, cut = ingest.ingest_dispatch(w)
        self.assertEqual((n, cut), (4, 1))
        w.commit()
        rows = find.search_days([])
        cut_rows = [r for r in rows if r.get("cut")]
        self.assertEqual(len(cut_rows), 1)
        self.assertEqual(cut_rows[0]["ch"], "inbox")
        self.assertIsNone(cut_rows[0]["mid"])

    def test_dispatch_channel_to_topic(self):
        self._put("dispatch_notify.log", DN_LIVE + "\n")
        w = store.Writer()
        ingest.ingest_dispatch(w)
        w.commit()
        by_k = {r["k"]: r for r in find.search_days([])}
        got = {(r["group"], r["topic_id"], r["topic_name"]) for r in by_k.values()}
        self.assertIn((ingest.GROUP_HQ, 1160, "Инбокс решений"), got)
        self.assertIn((ingest.GROUP_HQ, 328, "Постановка задач"), got)
        self.assertIn((ingest.GROUP_DM, None, "личка владельца"), got)

    def test_dispatch_keeps_full_text_and_mid(self):
        self._put("dispatch_notify.log", DN_LIVE + "\n")
        w = store.Writer()
        ingest.ingest_dispatch(w)
        w.commit()
        rows = find.search_days(["обрезку"])
        self.assertEqual(len(rows), 1, "полный текст новой строки не доехал")
        self.assertEqual(rows[0]["mid"], 9812)
        self.assertEqual(rows[0]["who_ref"], store.SELF_REF)
        self.assertGreater(len(rows[0]["text"]), 90)

    def test_dispatch_skips_non_result_lines(self):
        self._put("dispatch_notify.log", DN_LIVE + "\n")
        w = store.Writer()
        n, _ = ingest.ingest_dispatch(w)
        self.assertEqual(n, 4, "строка «хук=…» разобрана как сообщение")

    def test_delivery_jsonl(self):
        self._put("delivery_6m.jsonl", "\n".join([
            json.dumps({"date": "2026-06-26T05:03:59+00:00", "author": "6879003264",
                        "text": "Key code from the nmax is 718175"}, ensure_ascii=False),
            json.dumps({"date": "2026-06-26T04:53:04+00:00", "author": "659135499",
                        "text": "Right now, we can't find the car key"}, ensure_ascii=False),
            "не json вовсе",
        ]) + "\n")
        w = store.Writer()
        self.assertEqual(ingest.ingest_delivery(w), 2)
        w.commit()
        rows = find.search_days(["nmax"], group=ingest.GROUP_DELIVERY)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("6879003264", json.dumps(rows, ensure_ascii=False))

    # Конверт снят с живого `data_export/team/deramor.json`: имя файла — НИК собеседника,
    # `name` — его ИМЯ. Ровно этим личный чат и опасен для архива.
    EXPORT_DM = {
        "name": "Babushka Boi", "id": 879997398, "category": "team", "message_count": 2,
        "messages": [
            {"id": 7964, "date": "2024-12-03T07:54:21+00:00", "from_id": "879997398",
             "from": "Babushka Boi", "username": "deramor", "text": "поменял масло",
             "reply_to": None},
            {"id": 7965, "date": "2024-12-03T07:55:00+00:00", "from_id": "879997398",
             "from": "Babushka Boi", "username": "deramor", "text": "[PHOTO]", "reply_to": 7964},
        ]}

    EXPORT_GROUP = {
        "name": "Партнерка STM Turbobaby", "id": 1002751134848, "category": "partners",
        "message_count": 3,
        "messages": [
            {"id": 11, "date": "2026-05-01T10:00:00+00:00", "from_id": "111",
             "from": "A", "username": "aaa", "text": "нужен байк на Патонге", "reply_to": None},
            {"id": 12, "date": "2026-05-01T10:01:00+00:00", "from_id": "222",
             "from": "B", "username": "bbb", "text": "везу", "reply_to": None},
            {"id": 13, "date": "2026-05-01T10:02:00+00:00", "from_id": "333",
             "from": "C", "username": "ccc", "text": "принял", "reply_to": None},
        ]}

    def test_export_group_keeps_our_own_name(self):
        """Название ГРУППЫ — наша сущность, мы её сами так назвали: оно остаётся."""
        self._put(os.path.join("data_export", "partners", "Партнерка_STM.json"),
                  json.dumps(self.EXPORT_GROUP, ensure_ascii=False))
        w = store.Writer()
        self.assertEqual(ingest.ingest_export(w), 3)
        w.commit()
        m = store.read_manifest()["groups"]
        slug = [s for s in m if s.startswith("partners")][0]
        self.assertEqual(m[slug]["title"], "Партнерка STM Turbobaby")
        self.assertEqual(m[slug]["group_id"], 1002751134848)

    def test_export_personal_chat_is_pseudonymous(self):
        """У ЛИЧНОГО чата ник живёт в имени файла, а имя — в заголовке. В архив не идёт ни то,
        ни другое: ни каталогом, ни заголовком, ни путём источника."""
        self._put(os.path.join("data_export", "team", "deramor.json"),
                  json.dumps(self.EXPORT_DM, ensure_ascii=False))
        w = store.Writer()
        self.assertEqual(ingest.ingest_export(w), 2)
        w.commit()
        rows = find.search_days([])
        body = json.dumps(rows, ensure_ascii=False) + json.dumps(store.read_manifest(),
                                                                ensure_ascii=False)
        self.assertNotIn("Babushka Boi", body, "имя собеседника уехало в архив")
        self.assertNotIn("deramor", body, "ник собеседника уехал в архив (имя файла/каталог)")
        for slug, day, path in store.walk_days():
            self.assertNotIn("deramor", path, "ник собеседника стал именем каталога")
            self.assertTrue(slug.startswith("dm-p"), slug)
        photo = [r for r in rows if r["media"]]
        self.assertEqual(photo[0]["media"][0]["kind"], "photo")
        # Заголовка у личного чата в манифесте нет вовсе — не «пустая строка», а отсутствие
        # поля: положить туда было нечего, кроме имени человека.
        self.assertIsNone(store.read_manifest()["groups"][rows[0]["group"]].get("title"))

    def test_dry_run_writes_nothing(self):
        self._put("userbot.log", UB_LIVE + "\n")
        res = ingest.run(["userbot"], verbose=False, dry=True)
        self.assertTrue(res["dry"])
        self.assertEqual(res["would_write"], 3)
        self.assertEqual(store.stats()["messages"], 0)

    def test_second_full_run_is_harmless(self):
        self._put("userbot.log", UB_LIVE + "\n")
        self._put("dispatch_notify.log", DN_LIVE + "\n")
        first = ingest.run(["userbot", "dispatch"], verbose=False)
        second = ingest.run(["userbot", "dispatch"], verbose=False)
        self.assertEqual(first["written"], 7)
        self.assertEqual(second["written"], 0)
        self.assertEqual(second["index_rows"], 0)


# ─────────────────────────── 6. дверь поиска ───────────────────────────

class TestFind(_Rooted):
    def setUp(self):
        _Rooted.setUp(self)
        w = store.Writer()
        w.add("g1", self._rec("2026-09-05T10:00:00", "@a", "сломался задний тормоз у нмакс",
                              mid=1, topic=1160))
        w.add("g1", self._rec("2026-09-05T11:00:00", "@b", "везу байк в Раваи", mid=2, topic=328))
        w.add("g2", self._rec("2026-09-01T10:00:00", "@a", "тормоз в порядке", mid=3))
        w.commit()

    def test_words_are_and_by_default(self):
        self.assertEqual(len(find.search_days(["тормоз"])), 2)
        self.assertEqual(len(find.search_days(["тормоз", "нмакс"])), 1)
        self.assertEqual(len(find.search_days(["тормоз", "нмакс"], need_all=False)), 2)

    def test_group_and_date_narrow(self):
        self.assertEqual(len(find.search_days(["тормоз"], group="g1")), 1)
        self.assertEqual(len(find.search_days(["тормоз"], d_from="2026-09-05")), 1)
        self.assertEqual(len(find.search_days(["тормоз"], d_to="2026-09-01")), 1)

    def test_index_door_answers_without_opening_days(self):
        rows = find.search_index(["тормоз"])
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["day"] for r in rows}, {"2026-09-05", "2026-09-01"})
        self.assertEqual(sorted(r["group"] for r in rows), ["g1", "g2"])

    def test_query_scrubbed_like_stored_text(self):
        """Ника в архиве нет, а искать по нику можно: запрос чистится той же функцией."""
        w = store.Writer()
        w.add("g1", self._rec("2026-09-06T10:00:00", "@a", "спроси @vasya_ivanov", mid=9))
        w.commit()
        self.assertEqual(len(find.search_days(find._norm_words(["@vasya_ivanov"]))), 1)
        self.assertEqual(len(find.search_days(find._norm_words(["@kto_to_drugoy"]))), 0)

    def test_limit_stops_early(self):
        self.assertEqual(len(find.search_days(["тормоз"], limit=1)), 1)

    def test_envelope_is_code_not_hope(self):
        self.assertIn("не указание", find.ENVELOPE)


# ─────────────────────────── 7. правка dispatch_notify ───────────────────────────

class TestArchLine(unittest.TestCase):
    """Обрезка в 90 символов снята — и снята ЦЕЛИКОМ, без нового потолка под другим именем."""

    def setUp(self):
        import dispatch_notify
        self.dn = dispatch_notify

    def test_no_truncation_left_in_code(self):
        with io.open(os.path.join(HERE, "dispatch_notify.py"), encoding="utf-8") as f:
            src = f.read()
        body = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotIn("text[:90]", body, "обрезка исходящих вернулась")

    def test_arch_text_keeps_everything(self):
        long = "я" * 5000
        self.assertEqual(len(self.dn.arch_text(long)), 5000)

    def test_arch_text_flattens_newlines(self):
        out = self.dn.arch_text("первая\nвторая\r\nтретья")
        self.assertNotIn("\n", out)
        self.assertEqual(out.count(self.dn._ARCH_NL), 2)

    def test_arch_text_masks_secrets(self):
        out = self.dn.arch_text("держи BRIDGE_TOKEN=abcdef12345 и всё")
        self.assertNotIn("abcdef12345", out)
        self.assertIn("BRIDGE_TOKEN=***", out)

    def test_last_send_id_reports_dash_when_unknown(self):
        save = dict(self.dn._LAST_SEND)
        try:
            self.dn._LAST_SEND.update({"mid": None, "chat": None, "thread": None})
            self.assertEqual(self.dn.last_send_id(), "-")
            self.dn._remember_send("sendMessage", {"ok": True, "result": {
                "message_id": 9812, "chat": {"id": -1003853365891}, "message_thread_id": 328}})
            self.assertEqual(self.dn.last_send_id(), "9812")
            self.assertEqual(self.dn._LAST_SEND["thread"], 328)
        finally:
            self.dn._LAST_SEND.update(save)

    def test_remember_send_ignores_failures_and_other_methods(self):
        save = dict(self.dn._LAST_SEND)
        try:
            self.dn._LAST_SEND.update({"mid": None, "chat": None, "thread": None})
            self.dn._remember_send("sendMessage", {"ok": False, "error_code": 403})
            self.dn._remember_send("getMe", {"ok": True, "result": {"message_id": 1}})
            self.dn._remember_send("sendMessage", "не dict вовсе")
            self.assertEqual(self.dn.last_send_id(), "-")
        finally:
            self.dn._LAST_SEND.update(save)


# ─────────────────── 7. ЧАСЫ ЗАХВАТА: живой вызывающий (22.09.2026) ───────────────────
# Чего не хватало архиву с 05.09 — не склада, а ВЫЗЫВАЮЩЕГО. Здесь проверяется сам вызов:
# решение о частоте, штамп, откат, немота ветки ошибки и то, что демон его действительно зовёт.
# Боевого штампа `tmp/chatlog_tick/state.json` эти тесты НЕ КАСАЮТСЯ ни одной веткой: каждому
# тесту свой путь во временном каталоге.

class TestTickChasy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="turbobaby_TESTING_tick_")
        self.state = os.path.join(self.tmp, "state.json")
        self._save_off = os.environ.get(ingest.OFF_ENV)
        os.environ.pop(ingest.OFF_ENV, None)

    def tearDown(self):
        if self._save_off is None:
            os.environ.pop(ingest.OFF_ENV, None)
        else:
            os.environ[ingest.OFF_ENV] = self._save_off
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- решение о частоте: чистая функция ---

    def test_bez_shtampa_idem_srazu(self):
        """Ровно этот случай и есть 22.09: штампа нет, потому что захват не звали ни разу."""
        self.assertTrue(ingest.due({}, 1_700_000_000.0, 360))
        self.assertTrue(ingest.due(None, 1_700_000_000.0, 360))

    def test_svezhiy_shtamp_derzhit(self):
        now = 1_700_000_000.0
        self.assertFalse(ingest.due({"ran_at": now - 60}, now, 360))
        self.assertFalse(ingest.due({"ran_at": now - 359 * 60}, now, 360))

    def test_staryy_shtamp_puskaet(self):
        now = 1_700_000_000.0
        self.assertTrue(ingest.due({"ran_at": now - 360 * 60}, now, 360))
        self.assertTrue(ingest.due({"ran_at": now - 3 * 3600}, now, 60))

    def test_chasy_uehali_nazad_ne_zapirayut_zahvat(self):
        """Выход из сна и перевод времени дают штамп ИЗ БУДУЩЕГО. Без этой ветки архив встал бы
        молча и навсегда — ровно тем способом, каким он уже стоял 16 суток."""
        now = 1_700_000_000.0
        self.assertTrue(ingest.due({"ran_at": now + 10 ** 6}, now, 360))

    def test_nol_minut_otklyuchaet_chastotu(self):
        now = 1_700_000_000.0
        self.assertTrue(ingest.due({"ran_at": now}, now, 0))

    def test_CHATLOG_TICK_PURE(self):
        """Инвариант: решение о частоте не трогает ни диск, ни часы, ни окружение. Иначе его
        нельзя проверить регрессом, а непроверяемая частота — это и есть вставший архив."""
        import inspect
        src = inspect.getsource(ingest.due)
        for zapret in ("os.", "io.", "open(", "time.", "getenv", "environ"):
            self.assertNotIn(zapret, src, "due() перестала быть чистой: %s" % zapret)

    # --- оборот ---

    def _tick(self, **kw):
        kw.setdefault("state_path", self.state)
        return ingest.tick(**kw)

    def test_tick_zovet_zahvat_i_pishet_shtamp(self):
        zvali = []
        r = self._tick(now=1_700_000_000.0, every_min=360,
                       runner=lambda: (zvali.append(1) or {"written": 17, "dup": 4, "days": 2}))
        self.assertEqual(len(zvali), 1)
        self.assertEqual((r["ran"], r["ok"], r["written"]), (True, True, 17))
        st = ingest.read_tick_state(self.state)
        self.assertEqual(st["ran_at"], 1_700_000_000.0)
        self.assertEqual((st["runs"], st["written"], st["total_written"]), (1, 17, 17))

    def test_vtoroy_tick_srazu_ne_idet_na_disk(self):
        """Частоту решает модуль, а не звонящий: демон тикает раз в ~172с, а захват — раз в 6 ч."""
        zvali = []
        run = lambda: (zvali.append(1) or {"written": 3})
        self._tick(now=1_700_000_000.0, every_min=360, runner=run)
        r = self._tick(now=1_700_000_060.0, every_min=360, runner=run)
        self.assertEqual(len(zvali), 1, "захват пошёл на диск раньше срока")
        self.assertFalse(r["ran"])
        r = self._tick(now=1_700_000_000.0 + 6 * 3600, every_min=360, runner=run)
        self.assertEqual(len(zvali), 2)
        self.assertEqual(ingest.read_tick_state(self.state)["total_written"], 6)

    def test_otkat_flagom_ubivaet_vetku_celikom(self):
        zvali = []
        os.environ[ingest.OFF_ENV] = "1"
        r = self._tick(now=1_700_000_000.0, every_min=0,
                       runner=lambda: (zvali.append(1) or {"written": 1}))
        self.assertEqual(zvali, [], "CHATLOG_TICK_OFF не остановил захват")
        self.assertFalse(r["ran"])
        self.assertFalse(os.path.exists(self.state), "выключенная ветка тронула диск")

    def test_oshibka_ne_vynosit_tekst_perepiski(self):
        """ЗАПРЕТ ЗАДАНИЯ, ПОСТАВЛЕННЫЙ КОДОМ: содержимое переписки не выходит наружу НИ В ОДНОЙ
        ветке, включая ветку ошибки. `json.JSONDecodeError` цитирует разбираемый документ, а
        `UnicodeDecodeError` — сырые байты: положи `str(e)` в штамп — и переписка окажется в
        файле состояния, который никто не считает секретным."""
        tajna = "клиент просил скидку на нмакс до 12000"

        def padaet():
            raise ValueError(tajna)

        r = self._tick(now=1_700_000_000.0, every_min=0, runner=padaet)
        self.assertEqual((r["ran"], r["ok"], r["error"]), (True, False, "ValueError"))
        st = ingest.read_tick_state(self.state)
        self.assertEqual((st["last_error"], st["errors"]), ("ValueError", 1))
        with io.open(self.state, encoding="utf-8") as f:
            syroy = f.read()
        self.assertNotIn(tajna, syroy, "текст исключения (а с ним переписка) лёг в штамп")
        self.assertNotIn("скидку", syroy)
        self.assertNotIn(tajna, json.dumps(r, ensure_ascii=False))

    # --- взаимное исключение заходов (замер 22.09: 1118 дублей и 2 битых файла) ---

    def test_vtoroy_zahod_pri_vzyatom_zamke_ne_idet(self):
        """ЖИВОЙ КЛАСС, А НЕ ГИПОТЕЗА: дедуп читает лежащее ДО записи, поэтому два захвата,
        идущие одновременно, оба видят пустое место и оба дописывают."""
        lp = ingest.lock_path(self.state)
        self.assertTrue(ingest.lock_take(lp, 1_700_000_000.0))
        try:
            zvali = []
            r = self._tick(now=1_700_000_000.0, every_min=0,
                           runner=lambda: (zvali.append(1) or {"written": 9}))
            self.assertEqual(zvali, [], "второй захват пошёл на диск при взятом замке")
            self.assertFalse(r["ran"])
        finally:
            ingest.lock_drop(lp)

    def test_zamok_snimaetsya_posle_zahoda(self):
        lp = ingest.lock_path(self.state)
        self._tick(now=1_700_000_000.0, every_min=0, runner=lambda: {"written": 1})
        self.assertFalse(os.path.exists(lp), "замок остался висеть после удачного захода")
        self._tick(now=1_700_000_100.0, every_min=0, runner=lambda: (_ for _ in ()).throw(IOError()))
        self.assertFalse(os.path.exists(lp), "замок остался висеть после ПАДЕНИЯ захода")

    def test_broshennyy_zamok_perehvatyvaetsya(self):
        """Без этой ветки одно падение остановило бы архив навсегда и молча — тем самым
        способом, от которого мы его чиним."""
        lp = ingest.lock_path(self.state)
        self.assertTrue(ingest.lock_take(lp, 1_700_000_000.0))
        pozzhe = 1_700_000_000.0 + ingest.LOCK_STALE_S + 60
        self.assertTrue(ingest.lock_take(lp, pozzhe), "брошенный замок запер захват навсегда")
        ingest.lock_drop(lp)

    def test_shtamp_stavitsya_DO_zahoda(self):
        """2.2с захода — это окно, в которое влезает второй заход. Штамп после захода оставлял
        бы его открытым; проверяем заявку изнутри самого захода."""
        vidno = {}

        def dolgiy():
            vidno.update(ingest.read_tick_state(self.state))
            return {"written": 2}

        self._tick(now=1_700_000_000.0, every_min=360, runner=dolgiy)
        self.assertEqual(vidno.get("ran_at"), 1_700_000_000.0,
                         "штамп не заявлен до захода — окно гонки открыто")

    def test_shtamp_testa_ne_boevoy(self):
        """Тесты не касаются боевых файлов состояния — проверка самой ручки, а не обещания."""
        self.assertEqual(ingest.tick_state_path(self.state), self.state)
        self.assertNotEqual(ingest.tick_state_path(self.state), ingest.DEFAULT_TICK_STATE)
        self.assertTrue(ingest.DEFAULT_TICK_STATE.endswith(
            os.path.join("tmp", "chatlog_tick", "state.json")))


class TestDemonZovetZahvat(unittest.TestCase):
    """Вызывающий обязан БЫТЬ, а не быть написанным. Читаем исходник демона, а не верим шапке."""

    def setUp(self):
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as f:
            self.src = f.read()

    def _poll_once_kod(self):
        """Тело `poll_once` БЕЗ КОММЕНТАРИЕВ — исполняемые строки, а не текст.

        ПОЧЕМУ НЕ ПРОСТО ПОДСТРОКА (найдено мутантом M4, 22.09). Закомментированный вызов
        содержит ту же подстроку, что и живой: `assertIn('_chatlog_capture("poll")', telo)`
        зеленел на `# _chatlog_capture("poll")`. Тест, зелёный на мёртвом вызове, сторожем не
        является — он ровно та тишина, из-за которой архив простоял 16 суток."""
        i = self.src.index("def poll_once(")
        stroki = []
        for syraya in self.src[i:].split("\n")[1:]:
            if syraya.strip() and not syraya[:1].isspace():
                break                           # вышли из тела функции по отступу, а не по `def`
            s = syraya.split("#", 1)[0].strip()  # хвостовой комментарий тоже не код
            if s and not s.startswith('"""'):
                stroki.append(s)
        return stroki

    def test_demon_zovet_chatlog_ingest_tick(self):
        self.assertIn('"chatlog_ingest.py"), "--tick"', self.src,
                      "демон не зовёт захват — архив снова без часов")

    def test_vyzov_stoit_v_poll_once(self):
        self.assertIn('_chatlog_capture("poll")', self._poll_once_kod(),
                      "вызова нет среди ИСПОЛНЯЕМЫХ строк оборота")

    def test_heartbeat_ostalsya_posledney_strokoy(self):
        """На этом стоя́т О2 и О4: heartbeat говорит «оборот ЗАМКНУЛСЯ». Захват обязан встать
        ДО него, иначе новый вызов молча переехал бы смысл двух ожиданий полосы."""
        kod = self._poll_once_kod()
        self.assertEqual(kod[-1], "_write_heartbeat()", "heartbeat перестал быть последней строкой")
        self.assertLess(kod.index('_chatlog_capture("poll")'), kod.index("_write_heartbeat()"))

    def test_pod_testami_demon_ne_spavnit_zahvat(self):
        """ЗАМЕР, А НЕ ОСТОРОЖНОСТЬ: первый же прогон гейта после появления вызова доехал до
        живого `poll_once`, запустил ДВА настоящих захвата и положил в БОЕВОЙ архив 1118 дублей
        в 16 файлах дня плюс 2 побайтно испорченных файла. Проверка обязана отличаться от
        боевого пути — иначе гейт становится писателем."""
        i = self.src.index("def _chatlog_capture(")
        telo = self.src[i:self.src.index("\ndef ", i + 10)]
        self.assertIn('if "unittest" in sys.modules:', telo,
                      "гейт снова будет писать в боевой архив")
        self.assertLess(telo.index('"unittest" in sys.modules'), telo.index("subprocess.Popen"),
                        "замок стои́т ПОСЛЕ спавна — то есть не стои́т")

    def test_vyzov_bez_importa(self):
        """Спавн, а не импорт: ребра в графе нет — значит клиентское замыкание не растёт."""
        self.assertNotIn("import chatlog_ingest", self.src)
        self.assertNotIn("import chatlog_store", self.src)


# ───────── 8. ЧЕТЫРЕ ВЫДУМАННЫХ СООБЩЕНИЯ: что именно ложится в архив ─────────
# Все четыре придуманы здесь и сейчас; боевой архив не открывается ни на чтение, ни на запись.

class TestVydumannyeSoobshcheniya(_Rooted):
    def setUp(self):
        _Rooted.setUp(self)
        self.src = tempfile.mkdtemp(prefix="turbobaby_TESTING_vyd_")
        self._save_here = ingest.HERE
        ingest.HERE = self.src

    def tearDown(self):
        ingest.HERE = self._save_here
        shutil.rmtree(self.src, ignore_errors=True)
        _Rooted.tearDown(self)

    def _put(self, name, body):
        p = os.path.join(self.src, name)
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        return p

    def _export(self, msgs, cat="vydumannoe", fn="chat.json"):
        self._put(os.path.join("data_export", cat, fn),
                  json.dumps({"id": -100999888777, "name": "Выдуманная группа",
                              "messages": msgs}, ensure_ascii=False))

    # 1. обычное
    def test_obychnoe_soobshchenie(self):
        self._put("userbot.log",
                  "2026-09-22T05:00:00+00:00 | @vydumannyi | Выдуманный | "
                  "когда можно забрать выдуманный байк\n")
        w = store.Writer()
        self.assertEqual(ingest.ingest_userbot(w), 1)
        w.commit()
        rows = find.search_days([], group=ingest.GROUP_CLIENT_DM)
        self.assertEqual(len(rows), 1)
        self.assertIn("выдуманный байк", rows[0]["text"])
        self.assertTrue(rows[0]["who_ref"].startswith("p"), "автор не псевдонимизирован")
        self.assertNotIn("vydumannyi", json.dumps(rows, ensure_ascii=False))
        self.assertEqual(rows[0]["media"], [])

    # 2. с ответом-на
    def test_soobshchenie_s_otvetom_na(self):
        """`reply_to` есть ТОЛЬКО у выгрузок: `userbot.log` его не пишет, значит и взять неоткуда."""
        self._export([{"id": 11, "from_id": 501, "date": "2026-09-22T06:00:00",
                       "text": "сколько стоит выдуманная аренда на месяц"},
                      {"id": 12, "from_id": 502, "date": "2026-09-22T06:01:00",
                       "text": "выдуманный ответ по цене", "reply_to": 11},
                      {"id": 13, "from_id": 503, "date": "2026-09-22T06:02:00",
                       "text": "третий автор, чтобы чат считался группой"}])
        w = store.Writer()
        self.assertEqual(ingest.ingest_export(w), 3)
        w.commit()
        rows = find.search_days(["выдуманный ответ"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reply_to"], 11)
        self.assertEqual(rows[0]["mid"], 12)
        pervoe = find.search_days(["выдуманная аренда"])[0]
        self.assertIsNone(pervoe["reply_to"], "ответ-на появился там, где его не было")

    # 3. с фото
    def test_soobshchenie_s_foto_teryaet_fayl_no_ne_fakt(self):
        """ОТДЕЛЬНОЙ СТРОКОЙ: у фото записывается ТОЛЬКО отметка о факте вложения. Ни имени
        файла, ни ссылки — и это проверяется перебором ключей, а не чтением шапки."""
        self._export([{"id": 21, "from_id": 601, "date": "2026-09-22T07:00:00", "text": "[PHOTO]"},
                      {"id": 22, "from_id": 602, "date": "2026-09-22T07:01:00", "text": "[VOICE]"},
                      {"id": 23, "from_id": 603, "date": "2026-09-22T07:02:00",
                       "text": "выдуманная подпись к фото"}])
        w = store.Writer()
        ingest.ingest_export(w)
        w.commit()
        rows = find.search_days([])
        s_media = [r for r in rows if r["media"]]
        self.assertEqual(len(s_media), 2)
        vidy = sorted(m["kind"] for r in s_media for m in r["media"])
        self.assertEqual(vidy, ["photo", "voice"])
        for r in s_media:
            for m in r["media"]:
                self.assertEqual(sorted(m.keys()), ["kind", "note"],
                                 "в медиа завелось поле сверх отметки")
                for zapret in ("file", "path", "url", "link", "name", "id"):
                    self.assertNotIn(zapret, m, "адрес вложения уехал в архив")
        telo = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn(".jpg", telo)
        self.assertNotIn("http", telo)

    # 4. от бота
    def test_soobshchenie_ot_bota_lozhitsya_kak_obychnoe(self):
        """ЧЕСТНО: признака «это бот» в архиве НЕТ ни одного, и фильтра ботов тоже нет. Сообщение
        бота ложится неотличимо от человеческого — это свойство, а не поломка, и оно записано
        тестом, чтобы не считаться потом открытием."""
        self._put("userbot.log",
                  "2026-09-22T08:00:00+00:00 | @vydumannyi_bot | Выдуманный бот | "
                  "выдуманное автоуведомление о брони\n"
                  "2026-09-22T08:01:00+00:00 | id777000 | Telegram | выдуманный код входа\n")
        w = store.Writer()
        self.assertEqual(ingest.ingest_userbot(w), 2, "сообщение бота отброшено молча")
        w.commit()
        rows = find.search_days([], group=ingest.GROUP_CLIENT_DM)
        self.assertEqual(len(rows), 2)
        for r in rows:
            telo = json.dumps(r, ensure_ascii=False)
            # Слово `bot` в строке есть законно — это `src: userbot.log`, имя ИСТОЧНИКА.
            # Ловим НИК бота, а не подстроку: слишком широкий замок краснеет на своём же поле.
            self.assertNotIn("vydumannyi_bot", telo, "ник бота уехал в архив вместо псевдонима")
            self.assertNotIn("id777000", telo, "числовой id уехал в архив вместо псевдонима")
            self.assertNotIn("is_bot", r, "в архиве завёлся признак бота — его там не было")
            self.assertTrue(r["who_ref"].startswith("p"))
        self.assertEqual(len(find.search_days(["автоуведомление"])), 1)

    # общий замок на все четыре
    def test_chetyre_soobshcheniya_odnim_zahvatom_bez_lichnosti(self):
        self._put("userbot.log",
                  "2026-09-22T05:00:00+00:00 | @vydumannyi | Выдуманный Человек | обычное выдуманное\n"
                  "2026-09-22T08:00:00+00:00 | @vydumannyi_bot | Выдуманный Бот | выдуманное от бота\n")
        self._export([{"id": 31, "from_id": 701, "date": "2026-09-22T09:00:00", "text": "[PHOTO]"},
                      {"id": 32, "from_id": 702, "date": "2026-09-22T09:01:00",
                       "text": "выдуманный ответ-на", "reply_to": 31},
                      {"id": 33, "from_id": 703, "date": "2026-09-22T09:02:00", "text": "третий"}])
        st = ingest.run(["userbot", "export"], verbose=False)
        self.assertEqual(st["written"], 5)
        telo = json.dumps(find.search_days([]), ensure_ascii=False)
        for lichnost in ("Человек", "Бот", "vydumannyi", "@"):
            self.assertNotIn(lichnost, telo, "личность уехала в архив: %s" % lichnost)


if __name__ == "__main__":
    unittest.main(verbosity=2)
