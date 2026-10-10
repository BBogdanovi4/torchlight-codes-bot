"""Собирает с tlidb.com список навыков и предметов для справочника /skill и /item.

Запуск: python handbook/build_index.py  (пишет handbook/index.json)
Раз в неделю запускается на GitHub. Запросов к сайту немного: по одной странице на категорию и язык.
"""
import html
import json
import os
import re
import time
import urllib.request

UA = "Mozilla/5.0 (compatible; BodYan-handbook/1.0; +https://github.com/BBogdanovi4/torchlight-codes-bot)"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.json")

CATEGORIES = {
    "skill": ["Active_Skill", "Support_Skill", "Passive_Skill", "Activation_Medium_Skill",
              "Magnificent_Support_Skill", "Noble_Support_Skill", "Modularization_Skill",
              "Tartarus_Skill", "Origin_Tartarus_Skill"],
    "item": ["Legendary_Gear", "Pactspirit", "Destiny", "Ethereal_Prism", "Compass",
             "Season_Compass", "Probe"],
}
# Карточка в списке: ссылка с подсказкой и названием. Hyperlink это сноски в тексте, а не карточки.
CARD_RE = re.compile(r'<a data-hover="(/cache/(?:ru|en)/Torchlight_(?!Hyperlink)[^"]+)" href="([^"/]+)"[^>]*>([^<]+)</a>')


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def cards(lang, page):
    found = {}
    for hover, slug, name in CARD_RE.findall(fetch(f"https://tlidb.com/{lang}/{page}")):
        found.setdefault(slug, (html.unescape(name).strip(), hover))
    return found


def main():
    index = {"updated": time.strftime("%Y-%m-%d", time.gmtime()), "skill": [], "item": []}
    problems = []
    for kind, pages in CATEGORIES.items():
        seen = set()
        for page in pages:
            try:
                ru = cards("ru", page)
                time.sleep(1.5)  # бережём чужой сайт
                en = cards("en", page)
                time.sleep(1.5)
            except Exception as e:
                problems.append(f"{page}: {e}")
                continue
            if not ru:
                problems.append(f"{page}: на странице не нашлось карточек")
            for slug, (name_ru, hover) in ru.items():
                if slug in seen:
                    continue
                seen.add(slug)
                name_en = en.get(slug, (slug.replace("_", " "), ""))[0]
                # [ссылка, имя по-русски, имя по-английски, путь к подсказке, категория]
                index[kind].append([slug, name_ru, name_en, hover, page])
            print(f"{page}: {len(ru)}")
    for p in problems:
        print("Проблема:", p)
    if not index["skill"] or not index["item"]:
        raise SystemExit("Список пустой, старый index.json не трогаю")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Навыков: {len(index['skill'])}, предметов: {len(index['item'])}")


if __name__ == "__main__":
    main()
