# Справочник /skill и /item

Команды в Discord: `/skill` для навыков и `/item` для предметов (легендарки, питомцы, судьбы, призмы, компасы, зонды).
Пока печатаешь название, бот подсказывает варианты по-русски и по-английски. После выбора показывает карточку: теги, параметры, описание, иконку и ссылку на tlidb.com.

Как устроено:
- `build_index.py` раз в неделю собирает с tlidb.com список навыков и предметов в `index.json` (расписание `.github/workflows/handbook.yml`).
- `worker.js` сам бот, работает на Cloudflare Workers (бесплатно). Описание берёт с tlidb.com в момент запроса.
- `register_commands.py` один раз регистрирует команды на сервере BodYan.

## Подключение (один раз)

1. **Discord Developer Portal** (discord.com/developers/applications): New Application, имя «Справочник TLI».
   Понадобятся Application ID и Public Key со страницы General Information. В разделе Bot нажать Reset Token и скопировать токен.
2. **GitHub, секреты репозитория** (Settings > Secrets and variables > Actions): `DISCORD_APP_ID` и `DISCORD_BOT_TOKEN`.
3. **Cloudflare** (dash.cloudflare.com): Workers & Pages > Create > Worker, вставить код `worker.js`, Deploy.
   В Settings > Variables добавить `DISCORD_PUBLIC_KEY` = Public Key из шага 1.
4. **Discord Developer Portal**, General Information: в Interactions Endpoint URL вставить адрес воркера (`https://….workers.dev`) и сохранить. Discord сам проверит, что бот отвечает.
5. **Добавить на сервер:** открыть `https://discord.com/oauth2/authorize?client_id=<Application ID>&scope=applications.commands` и выбрать BodYan.
6. **GitHub:** Actions > «Справочник: обновить список» > Run workflow с галочкой «Зарегистрировать команды».

После этого в Discord появятся `/skill` и `/item`.
