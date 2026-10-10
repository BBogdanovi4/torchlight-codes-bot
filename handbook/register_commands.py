"""Регистрирует команды /skill и /item на сервере BodYan.

Нужно запустить один раз (и заново, если поменяются команды). На GitHub: Actions >
"Справочник: обновить список" > Run workflow с галочкой "Зарегистрировать команды".
Секреты репозитория: DISCORD_APP_ID (Application ID) и DISCORD_BOT_TOKEN (Bot > Token).
"""
import json
import os
import urllib.request

GUILD_ID = "1558461390925070366"  # сервер BodYan

COMMANDS = [
    {"name": "skill", "description": "Навык Torchlight: Infinite: теги, параметры и описание",
     "options": [{"type": 3, "name": "название", "description": "Начни вводить название навыка",
                  "required": True, "autocomplete": True}]},
    {"name": "item", "description": "Предмет Torchlight: Infinite: легендарки, питомцы, судьбы, компасы",
     "options": [{"type": 3, "name": "название", "description": "Начни вводить название предмета",
                  "required": True, "autocomplete": True}]},
]


def main():
    app_id = os.environ["DISCORD_APP_ID"].strip()
    token = os.environ["DISCORD_BOT_TOKEN"].strip()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/applications/{app_id}/guilds/{GUILD_ID}/commands",
        data=json.dumps(COMMANDS).encode("utf-8"), method="PUT",
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json",
                 "User-Agent": "DiscordBot (https://github.com/BBogdanovi4/torchlight-codes-bot, 1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print("Зарегистрированы команды:", ", ".join("/" + c["name"] for c in json.loads(r.read())))


if __name__ == "__main__":
    main()
