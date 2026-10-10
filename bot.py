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
import urllib.error
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
    if not isinstance(data, list) or (data and "isActive" not in data[0]):
        raise RuntimeError("API поменял формат ответа")  # иначе фильтр ниже молча вернёт пустой список
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


# (имя, функция, пустой ответ считается поломкой)
SOURCES = [
    ("TLI Compendium", from_compendium, False),  # активных кодов там иногда честно ноль
    ("PocketGamer", from_pocketgamer, True),     # список на странице никогда не бывает пустым
    ("Steam", from_steam, False),                # коды в новостях Steam редкость
]
BAD_RUNS_BEFORE_ALERT = 3  # разовый сбой сайта не повод писать в Discord


def collect():
    found, results = {}, []
    for name, source, empty_is_bad in SOURCES:
        try:
            codes = [c for c in source() if CODE_RE.match(c["code"])]
            for c in codes:
                found.setdefault(c["code"].lower(), c)
            problem = "отдаёт пустой список (похоже, сайт поменял вёрстку)" if empty_is_bad and not codes else ""
        except Exception as e:
            codes, problem = [], f"не отвечает ({type(e).__name__}: {e})"
        if problem:
            print(f"Источник {name}: {problem}")
        results.append((name, problem, len(codes)))
    return found, results


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


def send(url, payload, method="POST"):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
    time.sleep(1)  # у вебхуков Discord лимит около 5 сообщений за 2 секунды
    return json.loads(body) if body else None


def post(webhook, payload):
    # Имя и аватарка берутся из настроек вебхука в Discord: "Torchlight коды" для кодов, "Сторож" для сбоев.
    if not webhook:
        print("[вхолостую]", json.dumps(payload, ensure_ascii=False))
        return
    send(webhook, payload)


def update_status(state, webhook, results, crash):
    """Одно сообщение в канале статуса, которое бот правит при каждой проверке, а не пишет заново."""
    now = int(time.time())
    lines = [f"Последняя проверка: <t:{now}:R> (<t:{now}:t>)"]
    if crash:
        lines.append(f"❌ Бот {crash}")
    else:
        lines.append(" · ".join(f"{name} {'⚠️' if problem else '✅'} {count}" for name, problem, count in results))
    last = state.get("last_new")
    lines.append(f"Последний новый код: {last['code']}, <t:{last['at']}:R>" if last else "Последний новый код: пока не было")
    ok = not crash and not any(h.get("alerted") for h in state.get("health", {}).values())
    payload = {"embeds": [{
        "title": "🟢 Бот кодов работает" if ok else "🟡 Бот кодов работает с проблемами",
        "description": "\n".join(lines),
        "color": 0x2ECC71 if ok else 0xF1C40F,
        "footer": {"text": "Обновляется при каждой проверке, примерно раз в час. Если время давнее, бот не запускается."},
    }]}
    msg_id = state.get("status_message_id")
    if msg_id:
        try:
            send(f"{webhook}/messages/{msg_id}", payload, "PATCH")
            return
        except urllib.error.HTTPError as e:
            if e.code != 404:  # 404: сообщение удалили вручную, ниже создадим новое
                raise
    state["status_message_id"] = send(webhook + "?wait=true", payload)["id"]


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


def update_health(state, webhook, check, problem, threshold=BAD_RUNS_BEFORE_ALERT):
    """Пишет в Discord только при смене статуса: один раз когда сломалось, один раз когда починилось."""
    h = state.setdefault("health", {}).setdefault(check, {"bad_runs": 0, "alerted": False})
    if problem:
        h["bad_runs"] = min(h["bad_runs"] + 1, threshold)  # дальше не растёт, чтобы не коммитить каждый час
        if h["bad_runs"] >= threshold and not h["alerted"]:
            runs = f" уже {threshold} проверки подряд" if threshold > 1 else ""
            post(webhook, {"content": f"⚠️ Бот кодов, {check}: {problem}{runs}. Новые коды могут не прийти."})
            h["alerted"] = True
    else:
        if h["alerted"]:
            post(webhook, {"content": f"✅ Бот кодов, {check}: снова работает."})
        h["bad_runs"], h["alerted"] = 0, False


def check_codes(state, webhook):
    found, results = collect()
    seen = {c.lower() for c in state["codes"]}

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
            state["last_new"] = {"code": c["code"], "at": int(time.time())}
            save_state(state)
    print(f"Источники проверены, всего кодов: {len(found)}, новых: {len(new)}")
    return found, results


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK", "").strip()
    alert_webhook = os.environ.get("DISCORD_ALERT_WEBHOOK", "").strip() or webhook
    state = load_state()

    if "--seed" in sys.argv:
        found, _ = collect()
        state["codes"] += [c["code"] for c in found.values()]
        save_state(state)
        print("Запомнил как старые:", ", ".join(c["code"] for c in found.values()))
        return

    crash = ""
    try:
        found, results = check_codes(state, webhook)
    except Exception as e:
        found, results, crash = {}, [], f"упал с ошибкой {type(e).__name__}: {e}"
        print("Бот", crash)

    if not crash:
        update_health(state, alert_webhook, "проверка источников",
                      "" if found else "все источники пустые или не отвечают")
        if found:  # пока лежит всё сразу, хватит одного общего сообщения вместо отдельного про каждый сайт
            for name, problem, _ in results:
                update_health(state, alert_webhook, f"источник {name}", problem)
    update_health(state, alert_webhook, "сам бот", crash, threshold=1)

    if alert_webhook:
        try:
            update_status(state, alert_webhook, results, crash)
        except Exception as e:  # табличка статуса не должна ронять проверку кодов
            print("Не обновилось сообщение статуса:", e)

    # Раз в месяц меняем файл, чтобы в репозитории был коммит: в публичных репозиториях
    # GitHub выключает расписание после 60 дней без активности.
    state["heartbeat"] = time.strftime("%Y-%m", time.gmtime())

    if webhook:  # вхолостую состояние не сохраняем
        save_state(state)


if __name__ == "__main__":
    main()
