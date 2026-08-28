# Reelay

Локальный Telegram-бот: скачивает Instagram-видео, кладёт их в FIFO-очередь и публикует Reels по расписанию через официальный Meta API.

## Запуск

```bash
uv sync
uv run python -m reelay
```

При первом запуске владелец с Telegram username из `TELEGRAM_OWNER_USERNAME` отправляет `/start`; бот сохраняет numeric user ID и после этого игнорирует остальных.

Формат добавления:

```text
https://www.instagram.com/reel/SHORTCODE/
Необязательный caption — все строки после URL публикуются дословно.
```

Команды: `/queue`, `/file ID`, `/drop ID`, `/retry ID`, `/pause`, `/resume`.

Локальный процесс должен работать в моменты публикации. Пропущенные во время остановки слоты не догоняются.
