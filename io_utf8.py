# -*- coding: utf-8 -*-
"""
io_utf8.py — единый переключатель вывода процесса в UTF-8 (ПК-контур).

КЛАСС БАГА (укусил ТРИЖДЫ за 29–30.07.2026): текст с не-ASCII (кириллица, эмодзи 📊 ⏹ 🔔)
уходит наружу — print() в консоль/пайп, запись в файл, ответ кнопки, — а Python на Windows
кодирует его КОДИРОВКОЙ КОНСОЛИ (здесь cp1251). Эмодзи вне cp1251 роняют процесс
(`'charmap' codec can't encode character '\\U0001f4ca'` — это 📊), кириллица в UTF-8-приёмник
ложится мохибейком. Три отдельные починки (093119e — stdin журнала, dispatch_notify — stdin
хука, кнопка «Статус цепи») лечили ПО ОДНОМУ месту из трёх, и класс возвращался.

Здесь — общий шов на ПОЛОСУ ВЫВОДА: КАЖДЫЙ вход-процесс ПК-контура первой командой зовёт
force_utf8(), и его stdout/stderr гарантированно пишут UTF-8 — откуда бы его ни поднял
Планировщик, терминал или родитель субпроцессом. Страж класса — test_utf8_output_guard.py:
новый print-скрипт без force_utf8 или новый text-режим subprocess без encoding валит гейт.

Почему reconfigure, а НЕ PYTHONIOENCODING в окружении: env помогает только тем ДЕТЯМ, которых
мы сами спавним со своим env; процесс, поднятый Планировщиком/из терминала, про наш env не
знает. reconfigure чинит САМ процесс — независимо от того, кто и как его запустил. Пара к этому
шву на стороне ЧТЕНИЯ чужого вывода — encoding="utf-8" в каждом subprocess.run(text=True).
"""
import sys


def force_utf8():
    """stdout+stderr текущего процесса → UTF-8 (errors=replace). Идемпотентно и безопасно:
    подменённый в тесте поток без .reconfigure (StringIO) пропускаем, любую ошибку глотаем —
    переключатель вывода НЕ смеет ронять вызывающего (вывод вторичен по отношению к работе).
    → список реально переключённых потоков (для диагностики/тестов)."""
    switched = []
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue                      # StringIO/нестандартный поток — менять нечего и нечем
        try:
            reconfigure(encoding="utf-8", errors="replace")
            switched.append(name)
        except Exception:
            pass                          # поток закрыт/не поддерживает — не падаем на диагностике
    return switched
