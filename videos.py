"""Следит за новыми видео про Torchlight: Infinite на выбранных YouTube-каналах и пишет их в Discord.

У каждого YouTube-канала свой Discord-канал и свой вебхук (секрет GitHub).
Лента YouTube (RSS) сейчас не работает, поэтому бот читает страницу «Видео» канала.
Если секрета канала нет, бот только печатает, что отправил бы, и ничего не запоминает.
"""
import json
import os
import re
import sys
import time
import urllib.request

CHANNELS = {  # YouTube-канал: секрет с вебхуком его Discord-канала
    "EXMAGISTR": "DISCORD_VIDEOS_EXMAGISTR",
    "DonTheCrown": "DISCORD_VIDEOS_DONTHECROWN",
}
ONLY_TORCHLIGHT = re.compile(r"torchlight|\bTLI\b", re.I)  # авторы снимают и другие игры
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos_state.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
MAX_POSTS = 5  # на канал за один запуск, чтобы не завалить


def channel_page(handle):
    for attempt in range(3):  # YouTube иногда отдаёт страницу без данных, со второго раза обычно нормально
        req = urllib.request.Request(f"https://www.youtube.com/@{handle}/videos",
                                     headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8",
                                              "Cookie": "CONSENT=YES+1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            page = r.read().decode("utf-8", errors="replace")
        m = re.search(r"var ytInitialData = (\{.*?\});</script>", page, re.S)
        if m:
            break
        time.sleep(3)
    else:
        raise RuntimeError("на странице нет ytInitialData")
    avatar = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    videos = []

    def walk(o):
        if isinstance(o, dict):
            lv = o.get("lockupViewModel")
            if lv and lv.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
                meta = lv["metadata"]["lockupMetadataViewModel"]
                rows = meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                parts = [p.get("text", {}).get("content", "") for row in rows for p in row.get("metadataParts", [])]
                videos.append({"id": lv["contentId"], "title": meta["title"]["content"], "age": parts[-1] if parts else ""})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(m.group(1)))
    if not videos:
        raise RuntimeError("не нашёл ни одного видео, похоже YouTube поменял страницу")
    return videos, (avatar.group(1) if avatar else None)  # видео новые сверху


def is_fresh(age):  # «21 min ago», «5 hours ago», «1 day ago»: вышло не раньше вчера
    return bool(re.search(r"\b(second|minute|min|hour)s?\b|\b1 day\b|\b1d\b", age))


def post(webhook, payload):
    req = urllib.request.Request(webhook, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    urllib.request.urlopen(req, timeout=20).read()
    time.sleep(1)


def main():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    state.setdefault("channels", {})
    changed = False
    for handle, secret in CHANNELS.items():
        webhook = os.environ.get(secret, "").strip()
        known = state["channels"].get(handle)  # None: первый запуск для канала
        seen = set(known or [])
        try:
            videos, avatar = channel_page(handle)
        except Exception as e:
            print(f"Канал {handle}: {e}")
            continue
        new = []
        for v in videos:
            if not ONLY_TORCHLIGHT.search(v["title"]) or v["id"] in seen:
                continue
            if known is None and not is_fresh(v["age"]):
                seen.add(v["id"])  # старые видео при первом запуске просто запоминаем
            else:
                new.append(v)
        new = new[::-1][-MAX_POSTS:]  # старые первыми
        for v in new:
            payload = {"content": f"🎬 **{v['title']}**\nhttps://youtu.be/{v['id']}", "username": handle}
            if avatar:
                payload["avatar_url"] = avatar
            if not webhook:
                print(f"[вхолостую] {handle}: {v['title']}")
                continue
            post(webhook, payload)
            seen.add(v["id"])
        print(f"Канал {handle}: новых видео {len(new)}")
        if webhook:  # без вебхука ничего не запоминаем, чтобы настоящий запуск отправил свежие видео
            state["channels"][handle] = sorted(seen)
            changed = True
    if changed:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # видео не должны ронять проверку кодов
        print("Видео: ошибка", e)
        sys.exit(0)
