"""Следит за новыми кодами Torchlight: Infinite и пишет их в Discord через вебхук.

Запуск:
  python bot.py          проверить источники и отправить новые коды
  python bot.py --seed   запомнить все текущие коды как старые, ничего не отправляя
Без переменной DISCORD_WEBHOOK работает вхолостую: только печатает, что отправил бы.
"""
import html
import json
import os
import re
import sys
import time
import urllib.request

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_codes.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
CODE_RE = re.compile(r"^[A-Za-z0-9]{4,24}$")
HOW_TO = "Ввод: Меню > Настройки > Другое > Код обмена. Регистр букв важен."


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def from_compendium():
    data = json.loads(fetch("https://api.tlicompendium.com/api/giftcodes"))
    return [{"code": c["code"].strip(), "reward": c.get("rewardDescription") or "",
             "source": "TLI Compendium", "url": "https://tlicompendium.com/en"}
            for c in data if c.get("isActive")]


def from_pocketgamer():
    url = "https://www.pocketgamer.com/torchlight-infinite/codes/"
    page = fetch(url)
    m = re.search(r"Active[^<]*codes\s*</h2>.*?<ul>(.*?)</ul>", page, re.S | re.I)
    if not m:
        raise RuntimeError("не нашёл список активных кодов на странице")
    out = []
    for li in re.findall(r"<li>(.*?)</li>", m.group(1), re.S):
        text = html.unescape(re.sub(r"<[^>]+>", "", li)).replace("\xa0", " ").strip()
        parts = re.match(r"([A-Za-z0-9]+)\s*-?\s*(.*)", text)
        if parts:
            out.append({"code": parts.group(1), "reward": parts.group(2).strip(),
                        "source": "PocketGamer", "url": url})
    return out


def from_steam():
    data = json.loads(fetch(
        "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
        "?appid=1974050&count=10&maxlength=0&feeds=steam_community_announcements"))
    month_ago = time.time() - 30 * 86400
    out = []
    for item in data["appnews"]["newsitems"]:
        if item["date"] < month_ago:
            continue
        pattern = r"(?i)(?:redeem|gift|promo)\s*codes?\s*[:：]\s*(?:\[[^\]]*\]\s*)*([A-Za-z0-9]{4,24})"
        for code in re.findall(pattern, item["contents"]):
            out.append({"code": code, "reward": "", "source": "Steam (официальные новости)",
                        "url": item["url"]})
    return out


SOURCES = [from_compendium, from_pocketgamer, from_steam]


def collect():
    found, errors = {}, []
    for source in SOURCES:
        try:
            for c in source():
                if CODE_RE.match(c["code"]):
                    found.setdefault(c["code"].lower(), c)
        except Exception as e:
            errors.append(f"{source.__name__}: {e}")
    for e in errors:
        print("Ошибка источника", e)
    if len(errors) == len(SOURCES):
        sys.exit("Ни один источник не ответил")
    return found


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"announced": False, "codes": []}


def save_state(state):
    state["codes"] = sorted(set(state["codes"]), key=str.lower)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def post(webhook, payload):
    payload["username"] = "Torchlight коды"
    if not webhook:
        print("[вхолостую]", json.dumps(payload, ensure_ascii=False))
        return
    req = urllib.request.Request(webhook, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    urllib.request.urlopen(req, timeout=20).read()
    time.sleep(1)  # у вебхуков Discord лимит около 5 сообщений за 2 секунды


def code_message(c):
    lines = [f"Награда: {c['reward']}"] if c["reward"] else []
    lines.append(HOW_TO)
    return {
        "content": c["code"],  # отдельной строкой, чтобы удобно копировать с телефона
        "embeds": [{
            "title": "Новый код Torchlight: Infinite",
            "description": "\n".join(lines),
            "url": c["url"],
            "color": 0xF39C12,
            "footer": {"text": f"Нашёл на: {c['source']}"},
        }],
    }


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK", "").strip()
    state = load_state()
    found = collect()
    seen = {c.lower() for c in state["codes"]}

    if "--seed" in sys.argv:
        state["codes"] += [c["code"] for c in found.values()]
        save_state(state)
        print("Запомнил как старые:", ", ".join(c["code"] for c in found.values()))
        return

    if not state.get("announced") and webhook:
        post(webhook, {"content": "Бот подключён. Буду писать сюда новые коды Torchlight: Infinite, "
                                  "проверка примерно раз в час."})
        state["announced"] = True
        save_state(state)

    new = [c for key, c in found.items() if key not in seen]
    for c in new:
        post(webhook, code_message(c))
        if webhook:  # вхолостую ничего не запоминаем, чтобы настоящий запуск всё равно отправил
            state["codes"].append(c["code"])
            save_state(state)
    print(f"Источники проверены, всего кодов: {len(found)}, новых: {len(new)}")


if __name__ == "__main__":
    main()
