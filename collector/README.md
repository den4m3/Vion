# Vion Collector · by tomeeli

Отдельный сборщик метаданных релизов для Vion. Работает на Windows, NAS и VPS.
База хранится на вашем устройстве; регистрация в облаке Vion не требуется.

- [Проект Vion](https://github.com/den4m3/Vion)
- [Скачать выпуски](https://github.com/den4m3/Vion/releases)
- [Исходный код и актуальная инструкция](https://github.com/den4m3/Vion/tree/main/collector)

## Что делает

Собирает названия фильмов, год, дату публикации раздачи и доступные качества
1080p/2160p WEB-DL, BDRip и BDRemux. Один выбранный источник: Rutor напрямую
либо RuTracker, Kinozal, NNM-Club, seleZen через **ваш настроенный Jackett**.
Раздачи одного фильма объединяются; качества дополняются. Сериалы и CAM/TS
в эту ленту фильмов не входят. Дата публикации раздачи не означает дату
официальной цифровой премьеры. Новинки TMDB/ПоискКино в Vion остаются отдельными.

Загрузка выполняется в фоне раз в два часа, чтение готового каталога не ждёт
сети. Ошибка источника сохраняет предыдущую базу. Для каждого источника своя
база: переключение не удаляет остальные. Пустой ответ отмечается как ошибка,
а не как успешное обновление. Rutor: новая страница и постепенная догрузка
истории до 200 страниц; подтверждённая полнота истории не заявляется.

Первая версия передаёт текстовые метаданные релизов и IMDb ID, если он есть
в Jackett. Обложки и сведения о фильмах дополняет сам Vion через выбранный
каталог. Этот сборщик **не является** сервером полной синхронизации,
избранное/настройки/API-ключи Vion в него не загружаются. Ссылки на скачивание,
magnet, torrent-файлы и видеопотоки не хранятся и не публикуются.

## Windows

1. Скачайте `VionCollector-Windows-x64.zip` из Releases и распакуйте в постоянную
   папку. Готовый EXE не требует установки Python. Сборка пока без цифровой подписи.
2. Запустите `Start-Collector.cmd`. Консоль должна оставаться открытой.
   При первом запуске создаются `config.json` и папка `data`.
3. `Show-Key.cmd` показывает ключ подключения. Не публикуйте его.
4. В Vion: **Настройки → Новинки и релизы → Где работает сборщик →
   Сборщик Windows / NAS / VPS**. Откройте «Подключение внешнего сборщика»,
   введите `http://IP-КОМПЬЮТЕРА:8790` и ключ.
5. Нажмите «Обновить и проверить». Для домашней сети Windows Firewall должен
   разрешать входящие соединения к сборщику только в частном профиле.

Если запускаете из исходников, установите Python 3.10+ и используйте:

```powershell
py -3 collector.py --host 0.0.0.0 --port 8790
py -3 collector.py --show-key
```

`Ctrl+C` останавливает сборщик. Автозапуск можно добавить через Планировщик
заданий Windows: действие — EXE с аргументами `--host 0.0.0.0 --port 8790`,
рабочая папка — каталог EXE. Ключи в командную строку не добавляйте.

## NAS / Docker

Скачайте `VionCollector-Server.zip`, распакуйте и откройте терминал в папке:

```sh
docker compose up -d --build
docker compose logs --tail=30
docker compose exec vion-collector python /app/collector.py --data /data --config /data/config.json --show-key
```

В Vion адрес — `http://IP-NAS:8790`. Контейнер запускается от непривилегированного
пользователя; данные остаются в томе `vion-data` при пересборке/перезапуске.
Готовый публичный Docker-образ пока не выпускается: образ собирается на вашем NAS
для его архитектуры. Нужен Docker с поддержкой образа Python 3.12-slim.

Для настройки скопируйте `config.example.json` в `config.json`, отредактируйте
его и перенесите в контейнер:

```sh
docker compose cp config.json vion-collector:/data/config.json
docker compose restart
```

`docker compose down` сохраняет данные. Не используйте `down -v` без резервной
копии: этот вариант удаляет том.

## Linux / VPS

Нужны Python 3.10+, постоянный диск и доступ к выбранному источнику.
Для VPS через интернет нужны домен и HTTPS либо собственный VPN.

```sh
sudo useradd --system --home /var/lib/vion-collector --shell /usr/sbin/nologin vion
sudo install -d /opt/vion-collector
sudo install -o vion -g vion -m 700 -d /var/lib/vion-collector
sudo install -m 644 collector.py /opt/vion-collector/
sudo install -o vion -g vion -m 600 config.example.json /var/lib/vion-collector/config.json
sudo install -m 644 vion-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vion-collector
sudo -u vion python3 /opt/vion-collector/collector.py --data /var/lib/vion-collector --config /var/lib/vion-collector/config.json --show-key
```

Юнит слушает только `127.0.0.1:8790`. Установите HTTPS reverse proxy, например
Caddy, замените домен в `Caddyfile.example` и направьте его на этот порт.
Reverse proxy должен передавать заголовок `Authorization`. В Vion используйте
`https://ваш-домен`. Порт 8790 напрямую в интернет не открывайте.
Для NAS через Docker на VPS замените публикацию порта на `127.0.0.1:8790:8790`.
Если используете только VPN, слушайте VPN-адрес и ограничьте доступ firewall.

## Выбор источника

Настройки находятся в локальном `config.json` (для Docker — `/data/config.json`).
Редактируйте файл при остановленном сервисе и перезапустите его.

```json
{
  "source": "rutracker",
  "interval_minutes": 120,
  "pages_per_update": 2,
  "jackett_url": "http://192.168.1.10:9117",
  "jackett_key": "ВАШ-КЛЮЧ-JACKETT",
  "indexer": "rutracker"
}
```

`source`: `rutor`, `rutracker`, `kinozal`, `nnm-club`, `selezen` — строго один.
У Rutor можно указать `rutor_url` с HTTPS-зеркалом. У Jackett индексатор должен
быть добавлен, авторизован и успешно проходить Test. Его ID задаёт `indexer`.
Если сервис не поддерживает пустой запрос ленты, сборщик не придумывает
результаты и сохраняет предыдущий каталог с ошибкой обновления.
Вместо `jackett_key` можно задать переменную окружения `VION_JACKETT_KEY`.
При HTTP подключайте Jackett только в доверенной локальной сети/VPN; снаружи
используйте HTTPS. Ни конфигурация, ни ключ не включены в публичные архивы.

Период — 15–1440 минут. `pages_per_update` — 1–10 страниц Rutor за цикл;
при 1 обновляется только свежая страница, при 2+ догружается история.
Смена источника **в Vion** влияет на встроенный сборщик. Во внешнем режиме
источник выбирается **в config.json внешнего сборщика**.

## Данные, резерв и проверка

В `data`: `access-key.txt`, независимые `releases-ИСТОЧНИК.json` и курсоры.
Остановите сервис и скопируйте всю папку/том для резервной копии.
Ключ подключения даёт чтение каталога; интерфейса изменения по сети нет.
`--show-key` показывает его только по явному запросу; в обычном логе ключей нет.

```sh
python3 -m unittest -v test_collector.py
python3 collector.py --once
```

`GET /health` — только версия сервиса. `GET /v1/releases` — формат Vion schema=1,
заголовок `Authorization: Bearer КЛЮЧ`. До первой удачной загрузки возвращается
503; неправильный ключ — 401. Размер базы ограничен 8 MiB и 8000 карточками.
Никаких API для запуска произвольных команд или чтения файлов нет.

Тесты Windows и протокола выполняются отдельно. Наличие Dockerfile и systemd
юнита не означает, что проверена каждая модель NAS или конкретный VPS.
