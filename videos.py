"""Следит за новыми видео про Torchlight: Infinite на выбранных YouTube-каналах и пишет их в Discord.

Лента YouTube (RSS) сейчас не работает, поэтому бот читает страницу «Видео» канала.
Без переменной DISCORD_VIDEOS_WEBHOOK только печатает, что отправил бы, и ничего не запоминает.
"""
import json
import os
import re
import sys
import time
import urllib.request

CHANNELS = ["EXMAGISTR", "DonTheCrown"]
ONLY_TORCHLIGHT = re.compile(r"torchlight|\bTLI\b", re.I)  # каналы снимают и другие игры
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos_state.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
MAX_POSTS = 5  # за один запуск, чтобы не завалить канал


def channel_videos(handle):
    req = urllib.request.Request(f"https://www.youtube.com/@{handle}/videos",
                                 headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode("utf-8", errors="replace")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", page, re.S)
    if not m:
        raise RuntimeError("на странице нет ytInitialData")
    found = []

    def walk(o):
        if isinstance(o, dict):
            lv = o.get("lockupViewModel")
            if lv and lv.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
                meta = lv["metadata"]["lockupMetadataViewModel"]
                rows = meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                parts = [p.get("text", {}).get("content", "") for row in rows for p in row.get("metadataParts", [])]
                found.append({"id": lv["contentId"], "title": meta["title"]["content"], "age": parts[-1] if parts else ""})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(m.group(1)))
    if not found:
        raise RuntimeError("не нашёл ни одного видео, похоже YouTube поменял страницу")
    return found  # новые сверху


def is_fresh(age):  # «21 min ago», «5 hours ago», «1 day ago»: вышло не раньше вчера
    return bool(re.search(r"\b(second|minute|min|hour)s?\b|\b1 day\b|\b1d\b", age))


def post(webhook, text):
    req = urllib.request.Request(webhook, data=json.dumps({"content": text}).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    urllib.request.urlopen(req, timeout=20).read()
    time.sleep(1)


def main():
    webhook = os.environ.get("DISCORD_VIDEOS_WEBHOOK", "").strip()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = None  # первый запуск: старые видео запоминаем, свежие (за сутки) отправляем
    seen = set(state["seen"]) if state else set()
    new = []
    for handle in CHANNELS:
        try:
            videos = [v for v in channel_videos(handle) if ONLY_TORCHLIGHT.search(v["title"])]
        except Exception as e:
            print(f"Канал {handle}: {e}")
            continue
        for v in videos:
            if v["id"] in seen:
                continue
            if state is None and not is_fresh(v["age"]):
                seen.add(v["id"])
                continue
            new.append((handle, v))
    new = new[::-1][-MAX_POSTS:]  # старые первыми
    for handle, v in new:
        text = f"🎬 **{handle}**: {v['title']}\nhttps://youtu.be/{v['id']}"
        if not webhook:
            print("[вхолостую]", text.replace("\n", " "))
            continue
        post(webhook, text)
        seen.add(v["id"])
    print(f"Видео проверены, новых: {len(new)}")
    if webhook:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"seen": sorted(seen)}, f, ensure_ascii=False, indent=2)
            f.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # видео не должны ронять проверку кодов
        print("Видео: ошибка", e)
        sys.exit(0)
