# Бот кодов Torchlight: Infinite

Раз в час проверяет источники и пишет новые коды в Discord через вебхук.

Источники: API сайта TLI Compendium, страница кодов PocketGamer, официальные новости игры в Steam.

- `bot.py` сам бот.
- `seen_codes.json` коды, которые уже отправлены или известны как старые. Чтобы бот снова отправил код, удали его отсюда.
- `.github/workflows/check-codes.yml` расписание запуска на GitHub Actions.

Сторож: если источник ломается 3 проверки подряд (ошибка или пустой список) или падает сам бот, приходит одно сообщение о поломке и одно о починке. Сбои идут в секрет `DISCORD_ALERT_WEBHOOK`, а если его нет, то в канал кодов.

URL вебхука хранится в секрете репозитория `DISCORD_WEBHOOK` (Settings > Secrets and variables > Actions).
Ручной запуск: вкладка Actions > "Проверка кодов Torchlight" > Run workflow.
