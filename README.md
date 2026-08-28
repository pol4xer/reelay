# Reelay

Локальный Telegram-бот: скачивает Instagram-видео, кладёт их в FIFO-очередь и публикует Reels по расписанию через официальный Meta API.

## Установка и ручной запуск

```bash
make install
make run
```

При первом запуске владелец с Telegram username из `TELEGRAM_OWNER_USERNAME` отправляет `/start`; бот сохраняет numeric user ID и после этого игнорирует остальных.

Формат добавления:

```text
https://www.instagram.com/reel/SHORTCODE/
Необязательный caption — все строки после URL публикуются дословно.
```

После ссылки бот ждёт одно следующее текстовое сообщение и использует его как caption. Если следующим сообщением приходит новая Instagram-ссылка, это считается новым видео.

Видео не формата 9:16 помещаются целиком на холст 1080×1920 с размытым фоном. Бот создаёт случайно от 15 до 20 тегов: сначала исходные и точные тематические, затем соседние по теме и несколько широких охватных. Идея определяется по caption, исходному описанию, тексту и объектам в кадрах через Apple Vision.

Команда `/posts N` меняет количество ежедневных публикаций от 1 до 12 и равномерно распределяет их между `POST_WINDOW_START` и `POST_WINDOW_END`. Без аргумента `/posts` показывает активное расписание.

Нативная кнопка `Menu` в Telegram показывает список команд с краткими описаниями. `/help` присылает полную памятку и текущее расписание.

При `ALLOW_PRIVATE_SOURCES=false` бот не читает cookies Chrome. Недоступные, закрытые и удалённые публикации удаляются из очереди с коротким сообщением `Пропущено: #ID`.

Facebook Page, Threads и YouTube publishers уже подключены к общей очереди, но по умолчанию выключены. Каждый внешний media ID сохраняется сразу: если одна платформа упала, `/retry` продолжит с неё и не продублирует уже успешные публикации. Настройка credentials: [`docs/CROSSPOSTING_SETUP.md`](docs/CROSSPOSTING_SETUP.md).

Команды: `/help`, `/status`, `/queue`, `/file ID`, `/drop ID`, `/retry ID`, `/now`, `/posts N`, `/pause`, `/resume`.

## Структура приложения

Код разделён по одной ответственности:

- `reelay/app.py` — сборка приложения и зависимостей;
- `reelay/bot.py` — только Telegram UI и команды;
- `reelay/downloader.py` — получение и нормализация Instagram MP4;
- `reelay/publishers/` — отдельный uploader для Instagram, Facebook, Threads и YouTube;
- `reelay/publishers/contract.py` — единый строгий `PublishRequest → PublishResult`;
- `reelay/services/publishing.py` — очередь, порядок платформ и checkpoints;
- `reelay/scheduler.py` — только расчёт времени и запуск publishing service;
- `reelay/media/` — подготовка платформо-специфичного видео;
- `reelay/transports/` — временный Cloudflare Quick Tunnel;
- `reelay/db.py` — SQLite persistence и журнал publish attempts.

Статус `published` выставляется только после сохранения ID всех включённых платформ. Если процесс
прервался, незавершённое задание возвращается в очередь при следующем старте.

## Форматирование и проверки

```bash
make format
make check
make queue-audit
```

`make format` применяет Ruff ко всему Python-коду. `make check` проверяет lint и форматирование,
компилируемость Python, shell script и LaunchAgent plist; type-check и тесты не запускаются.
`make queue-audit` сверяет SQLite checkpoints, наличие файлов и ffprobe metadata всех ожидающих
MP4.

## Панель настроек

Вместо ручного редактирования `.env` можно открыть локальную панель:

```bash
make config-ui
```

На macOS также можно дважды нажать `Reelay Settings.command` в папке проекта. Панель открывается
на `http://127.0.0.1:8765`, разделяет Telegram, Instagram/Meta, Facebook, Threads, YouTube и
расписание по отдельным вкладкам, показывает подсказки и официальные ссылки. Секреты не
возвращаются из backend в браузер: UI видит только факт, что поле уже заполнено.

Кнопка «Сохранить» атомарно обновляет локальный `.env`. «Сохранить и перезапустить» применяет
конфигурацию к LaunchAgent; если в этот момент идёт скачивание или публикация, restart будет
отложен с понятной ошибкой, а сохранённые значения останутся на диске.

## Автозапуск на macOS

Reelay может работать как пользовательский macOS LaunchAgent и не зависит от запущенного
Terminal, Codex или ChatGPT:

```bash
make service-install
make service-status
```

`service-install` создаёт `~/Library/LaunchAgents/com.pol4xer.reelay.plist`, запускает Reelay
с помощью `.venv/bin/python -m reelay` и включает:

- запуск после входа пользователя в macOS;
- автоматический перезапуск процесса через `KeepAlive`;
- продолжение работы после выхода из Codex;
- запись stdout в `data/logs/reelay.log`, stderr — в `data/logs/reelay.error.log`.

Управление процессом:

```bash
make service-start
make service-stop
make service-status
make service-uninstall
```

Reelay держит process lock в `data/reelay.lock`, поэтому второй ручной или системный экземпляр
не сможет одновременно менять очередь и создавать дубли. `service-start` также отказывается
прерывать задание со статусом `publishing`. Файл `.env` остаётся в корне проекта и не копируется
в LaunchAgent.

При закрытой крышке Mac обычно засыпает, поэтому Reelay не исполняется до пробуждения. После
пробуждения уже запущенный процесс продолжит работу; после перезагрузки LaunchAgent стартует при
следующем входе пользователя. APScheduler догоняет слот только в течение
`SCHEDULE_GRACE_MINUTES` (по умолчанию 30 минут); более старый пропуск не создаёт несколько
публикаций подряд.

При холодном старте Reelay отдельно проверяет последний слот: если он был не больше
`SCHEDULE_GRACE_MINUTES` назад и в журнале нет попытки, создаётся ровно один catch-up запуск.

LaunchAgent относится только к локальному macOS-запуску. Серверный деплой позднее сможет
использовать тот же стабильный entrypoint `python -m reelay`, не меняя код приложения.

## Проверка Linux-сервера

Перед серверным деплоем передайте владельцу только `scripts/server-preflight.sh`. Скрипт ничего
не устанавливает, не читает credentials и удаляет созданные временные файлы. Запуск:

```bash
chmod +x server-preflight.sh
./server-preflight.sh | tee reelay-server-report.txt
```

Файл `reelay-server-report.txt` содержит ОС, архитектуру, доступные CPU/RAM/диск, SSH-контекст,
systemd/Docker/tooling, проверку SQLite WAL и доступность всех необходимых DNS/HTTPS endpoint'ов.
По умолчанию проверяется будущий runtime-путь `/opt/reelay/data`; другой абсолютный путь можно
передать единственным аргументом скрипта.

## Docker

Linux-контейнер включает Python 3.13, frozen dependencies, FFmpeg/ffprobe и multi-arch
`cloudflared`. Запуск из `/opt/reelay`:

```bash
sudo install -d -o 10001 -g 10001 -m 0700 /opt/reelay/data
docker compose up --detach --build
docker compose logs --follow --tail=100 reelay
```

Compose монтирует `./data` в `/app/data`, автоматически перезапускает контейнер и не открывает
входящие порты. Nginx и TLS для основного бота не требуются: Telegram использует long polling,
публикации идут исходящими HTTPS-запросами, а Threads получает одноразовый HTTPS Quick Tunnel.
Подробности и перенос существующей очереди: [`deploy/docker/README.md`](deploy/docker/README.md).

Один полный архив с кодом, `.env`, SQLite и всеми ожидающими MP4 создаётся командой:

```bash
make server-bundle
```

Перед созданием архива локальный Reelay нужно остановить (`make service-stop`). Сборщик
дополнительно проверяет process lock и откажется делать потенциально расходящийся снимок.

На Linux-сервере с установленными Docker Engine, Docker Compose v2 и `unzip` достаточно
одной строки (подставьте SHA-256, который напечатает сборщик и который будет указан рядом
с готовым архивом):

```bash
echo 'SHA256  Reelay-All-In-One.zip' | sha256sum --check && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && unzip -q Reelay-All-In-One.zip -d "$d" && sudo bash "$d/reelay-server/deploy/docker/install.sh"
```
