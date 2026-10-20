# Приложение 4 к договору аренды парка — зелёные книги и фото 38 байков

Договор: Long-Term Motorcycle Rental Agreement от 20.10.2026, Mr. Filipp Vorobev → TB Mobility Co., Ltd.
Приложение 4 идёт по Приложению 1 договора (38 байков, тот же порядок). Для каждого байка:

1. лицевая обложка зелёной книги (на ней все номера машины);
2. страница регистрации, где ผู้ถือกรรมสิทธิ์ = MR. FILIPP VOROBEV, с моделью и номером кузова;
3. фото байка, на котором виден номер.

Чего не хватает, ставится рамкой «จะจัดส่งเพิ่มเติม / To be provided», а в оглавлении — знаком ○.
Разбор, числа и остаток: [`docs/artifacts/2026-10-20-annex4-green-books-photos.md`](../docs/artifacts/2026-10-20-annex4-green-books-photos.md).

## Файлы

| файл | что |
|---|---|
| `annex_map.json` | 38 байков: имя дословно как в договоре, номер по книге, кузов, источник обложки и страницы (id файла на Диске, страница, рамка документа), статус |
| `build_annex.py` | `prepare`: PDF с Диска → обрезанные страницы книги; `html`: карта + картинки → HTML |
| `print_pdf.js` | HTML → PDF через Chromium (playwright), колонтитул с номером страницы |
| `../annex_bike_photos.py` | **на ПК**: снимки из темы каждого байка в форуме Обслуживания → `tmp/annex_bike_photos.zip` |

Статусы в `annex_map.json`: `ok` — страница с Филиппом есть и сверена; `file04` — страница только в
сводном скане «04» (15,7 МБ, коннектор Drive отдаёт до 10 МБ); `owner_will_provide` — №23 (книги нет),
№28 и №31 (не переоформлены на Филиппа), владелец дошлёт.

## Как пересобрать (облачная сессия)

```
# шрифты: npm pack @fontsource/noto-sans-thai@5.1.1 @fontsource/noto-serif-thai@5.1.1 @fontsource/noto-sans@5.1.1
#         и распаковать каждый в <fonts>/fontsource-<имя>-5.1.1/
# источники: скачать PDF с Диска коннектором и положить как <src>/<drive_id>.pdf
python3 annex4/build_annex.py prepare --map annex4/annex_map.json --src <src> --img <img> --work <work>
# фото: положить в <img> как NN_photo.jpg (NN = номер в Приложении 1), сверив номер на снимке с книгой
python3 annex4/build_annex.py html --map annex4/annex_map.json --img <img> --fonts <fonts> --out annex.html --draft ""
NODE_PATH=$(npm root -g) node annex4/print_pdf.js $PWD/annex.html $PWD/annex.pdf
```

Картинки и готовый PDF в git **не кладутся**: на страницах книг — номер паспорта и адрес владельца.

## Как добрать фото (ПК)

```
venv\Scripts\python.exe annex_bike_photos.py --dry      # сколько снимков в теме каждого байка
venv\Scripts\python.exe annex_bike_photos.py            # скачать → tmp\annex_bike_photos.zip
```

Скрипт только читает: копия сессии юзербота, ни одной отправки. По каждому байку берёт 12 самых свежих
и 4 самых ранних снимка его темы. У №25 (XSR 155 GREEN) своей темы в `topics_map.json` нет: укажите её
ключом `--topic 25=<номер темы>`. Архив загрузите на Диск, а кадр с читаемым номером выбирает сессия,
которая собирает приложение.
