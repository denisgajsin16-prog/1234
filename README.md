# Telegram Bot: Справочник + Квиз (aiogram 3) — WEBHOOK

Эта версия работает через **webhook**, поэтому на хостингах типа Render/Railway бот может "просыпаться" от сообщения в Telegram.

## Переменные окружения
Задай в панели хостинга (или в `.env`, но `.env` в GitHub не коммить):

```env
BOT_TOKEN=PASTE_YOUR_TOKEN_HERE
BASE_URL=https://YOUR_PUBLIC_DOMAIN   # домен от Railway/Render, без слеша в конце
WEBHOOK_PATH=/tg-webhook              # можно оставить так
```

Порт берётся из env `PORT` (Railway/Render задают автоматически).

## Команды деплоя
Build: `pip install -r requirements.txt`
Start: `python bot.py`
