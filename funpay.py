"""Следит за лотами продавцов на FunPay и пишет изменения в Discord.

Сообщает: новый лот, лот снят или продан, цена изменилась, снятый лот вернулся (с той же или другой ценой).
Плюс одна табличка «Витрина», которая переписывается при каждой проверке: что продавец выставил сейчас.
Если секрета DISCORD_FUNPAY нет, бот только печатает, что отправил бы, и ничего не запоминает.
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

SELLERS = ["5070920"]  # id продавцов из ссылки funpay.com/users/<id>/
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "funpay_state.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
KEEP_GONE_DAYS = 30  # столько помним снятые лоты, чтобы узнать их, если вернутся
GREEN, RED, YELLOW, BLUE, GREY = 0x2ECC71, 0xE74C3C, 0xF1C40F, 0x3498DB, 0x95A5A6


def fetch(url, currency="rub"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru",
                                               "Cookie": f"cy={currency}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def prices_in(uid, currency):
    page = fetch(f"https://funpay.com/users/{uid}/", currency)
    return {i: float(p) for i, p in re.findall(
        r'offer\?id=(\d+)" class="tc-item.*?class="tc-price" data-s="([\d.]+)"', page, re.S)}


def seller_page(uid):
    page = fetch(f"https://funpay.com/users/{uid}/")
    if "Дата регистрации" not in page:
        raise RuntimeError("страница продавца не открылась или FunPay её поменял")
    name = re.search(r"<title>Пользователь (.+?) / FunPay", page)
    lots = {}
    # Лоты сгруппированы по разделам: заголовок раздела, потом таблица с лотами.
    for section in re.split(r'<div class="offer-list-title">', page)[1:]:
        category = text(re.search(r"<h3>(.*?)</h3>", section, re.S).group(1))
        for m in re.finditer(r'<a href="(https://funpay\.com/lots/offer\?id=(\d+))" class="tc-item.*?</a>', section, re.S):
            block = m.group(0)
            desc = re.search(r'<div class="tc-desc-text">(.*?)</div>', block, re.S)
            price = re.search(r'class="tc-price" data-s="([\d.]+)"', block)
            if not desc or not price:
                continue
            lots[m.group(2)] = {"title": text(desc.group(1)), "category": category,
                                "price": float(price.group(1)), "url": m.group(1)}
    # Продавец ставит цену в одной валюте, остальные FunPay пересчитывает по курсу, и они плавают.
    # Поэтому запоминаем цену во всех трёх валютах: настоящая смена цены меняет все три.
    other = {c: prices_in(uid, c) for c in ("usd", "eur")}
    for lid, lot in lots.items():
        lot["prices"] = {"rub": lot["price"], **{c: other[c].get(lid) for c in other}}
    rating = re.search(r'<div class="rating-value[^>]*><span class="big">([\d.,]+)</span>', page)
    count = re.search(r"(\d+) отзыв\w* за", page)
    reviews = [text(d) + (": " + text(t) if text(t) else "") for d, t in re.findall(
        r'<div class="review-item-detail">(.*?)</div>.*?<div class="review-item-text">(.*?)</div>', page, re.S)]
    avatar = re.search(r'avatar-photo" style="background-image: url\(([^)]+)\)', page)
    return {"name": name.group(1) if name else uid, "lots": lots, "avatar": avatar.group(1) if avatar else None,
            "rating": rating.group(1) if rating else None,
            "review_count": int(count.group(1)) if count else len(reviews), "reviews": reviews}


# --- Разбор описания лота Arena Breakout: схрон и нал в «кк» (порт парсера из «ABI Прайсер», 1:1) ---
UNIT = r"(?:ккк|kkk|трлн|млрд|миллиард|миллион|млн|лям|тысяч|тыс|кк|kk|mil|[мmкk](?![а-яёa-z]))"
STASH_WORDS = ["схрон", "склад", "хранилищ", "stash", "storage", "стеш", "стэш", "запас"]
CASH_WORDS = [r"(?<![а-яёa-z])нал(?!ичи)", "cash", "коэн", "coen"]  # «наличие» не нал
UNIT_MULT = [("ккк", 1000), ("kkk", 1000), ("трлн", 1e6), ("млрд", 1000), ("миллиард", 1000), ("миллион", 1),
             ("млн", 1), ("лям", 1), ("кк", 1), ("kk", 1), ("mil", 1), ("тысяч", 0.001), ("тыс", 0.001),
             ("м", 1), ("m", 1), ("к", 0.001), ("k", 0.001)]
NUM = r"(\d[\d.,]*\d|\d)"
PREP = r"(?:\s+(?:на|в|во|всего|это)(?![а-яёa-z]))?"
SP = "[    ]"


def _unit_mult(u):
    if not u:
        return None
    u = u.lower()
    return next((m for w, m in UNIT_MULT if u.startswith(w)), None)


def _val(s0):
    s = re.sub(SP, "", s0)
    if s.count(".") + s.count(",") >= 2:
        s = re.sub(r"[.,]", "", s)
    else:
        m = re.fullmatch(r"(\d{4,})[.,](\d{3})", s)
        s = m.group(1) + m.group(2) if m else s.replace(",", ".", 1)
    m = re.match(r"\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def _to_kk(s, u):
    v = _val(s)
    if v is None:
        return 0
    if v >= 100000:
        return v / 1e6
    m = _unit_mult(u)
    return v * m if m is not None else v


def _accept(s, u):
    v = _val(s)
    if v is None or (u is None and v < 100000):
        return 0
    kk = _to_kk(s, u)
    return kk if kk < 100000 else 0


def _accept2(s, u):
    kk = _to_kk(s, u)
    return kk if kk < 100000 else 0


def _normalize(text_):
    d = re.sub(r"(?<=[\d.,])[ОоOo]+", lambda m: "0" * len(m.group(0)), text_)
    for _ in range(4):
        d2 = re.sub(r"(\d)" + SP + r"(?=\d{3}(?:\D|$))", r"\1", d, count=1)
        if d2 == d:
            break
        d = d2
    return d


def _grab(d, keys):
    best = 0
    for k in keys:
        for m in re.finditer(k + r"[а-яёa-z]{0,4}" + PREP + r"[^\dа-яёa-z]{0,6}" + NUM + r"\s*\+?\s*(" + UNIT + ")?", d, re.I):
            best = max(best, _accept(m.group(1), m.group(2)))
        for m in re.finditer(NUM + r"\s*\+?\s*(" + UNIT + r")?[а-яёa-z]{0,3}" + PREP + r"[^\dа-яёa-z]{0,6}" + k, d, re.I):
            best = max(best, _accept(m.group(1), m.group(2)))
        for m in re.finditer(NUM + r"\s*\+?\s*(" + UNIT + r")?\s+(?:[а-яё]+\s+){0,2}(?:стоимост|ценност|общ)"
                             r"[а-яёa-z0-9_]*\s+(?:[а-яё]+\s+){0,2}" + k, d, re.I):
            best = max(best, _accept2(m.group(1), m.group(2)))
        for m in re.finditer(r"(?<!\+)(\d{2,})(?=" + k + ")", d, re.I):
            best = max(best, _accept2(m.group(1), None))
        for m in re.finditer(k + r"(\d{2,})", d, re.I):
            best = max(best, _accept2(m.group(1), None))
    return best


def _spans(d, keys):
    return [m.span() for k in keys for m in re.finditer(k, d, re.I)]


def _assign(d, sspans, cspans):  # каждое число отдаём ближайшему слову: схрону или налу
    nums = []
    for m in re.finditer(NUM + r"\s*\+?\s*(" + UNIT + ")?", d):  # без re.I, как в оригинале
        v = _accept(m.group(1), m.group(2))
        if v > 0:
            nums.append((m.start(), m.end(), v))
    if not nums:
        return 0, 0

    def gap(ns, ne, spans):
        return min(((ns - b) * 2 if b <= ns else (a - ne) * 2 + 1 if a >= ne else 0) for a, b in spans)

    s_cand = [(g, i, v) for i, (ns, ne, v) in enumerate(nums) if (g := gap(ns, ne, sspans)) <= 18]
    c_cand = [(g, i, v) for i, (ns, ne, v) in enumerate(nums) if (g := gap(ns, ne, cspans)) <= 18]
    best = None
    for so in [None] + s_cand:
        for co in [None] + c_cand:
            if so and co and so[1] == co[1]:
                continue
            key = (-((1 if so else 0) + (1 if co else 0)), (so[0] if so else 0) + (co[0] if co else 0), 0 if so else 1)
            if best is None or key < best[0]:
                best = (key, so[2] if so else 0, co[2] if co else 0)
    return best[1], best[2]


def stash_cash(desc):
    d = _normalize(desc or "")
    sg, cg = _grab(d, STASH_WORDS), _grab(d, CASH_WORDS)
    ss, cs = _spans(d, STASH_WORDS), _spans(d, CASH_WORDS)
    if not (ss and cs):
        return sg, cg
    ps, pc = _assign(d, ss, cs)
    stash = ps if ps > 0 else (0 if sg == cg and sg > 0 else sg)
    cash = pc if pc > 0 else (0 if sg == cg and cg > 0 else cg)
    return stash, cash


def mail_status(desc):  # «без»: без почты или смена почты (риск, в ABI почту не сменить); «с»: почта передаётся
    low = desc.lower()
    if re.search(r"без\s*почт|без[а-яё\s]{0,12}почт|смен[а-яё]*\s*(почт|данн|мыл)|сменю\s*почт|поменя[а-яё]*\s*почт|"
                 r"почт[а-яё]*\s*(не\s*перед|не\s*дам|не\s*отда|остав|мо[яёе]|сво[яё]|у\s*мен)|"
                 r"(не\s*дам|не\s*отда|оставля|оставлю|остав[а-яё]*\s*себе)[а-яё\s]{0,6}почт", low):
        return "без"
    if re.search(r"родн[а-яё]*\s*почт|почт[а-яё]*\s*(в\s*комплект|перед[аёя]|с\s*акк|идёт|иду)|с\s*почт|"
                 r"gmail|firstmail|outlook|gmx|rambler", low):
        return "с"
    return "?"


def kk(v):
    return f"{v:.1f}кк" if 0 < v < 10 else f"{round(v)}кк"


def abi_info(lot):
    """Схрон, нал, $ за кк и почта для лота Arena Breakout, None для остальных."""
    if "arena breakout" not in (lot["category"] + lot["title"]).lower():
        return None
    stash, cash = stash_cash(lot["title"][:2000])
    usd = (lot.get("prices") or {}).get("usd")
    return {"stash": stash, "cash": cash, "ppk": usd / stash if stash > 0 and usd else None,
            "mail": mail_status(lot["title"])}


def abi_line(lot):
    a = abi_info(lot)
    if not a:
        return ""
    parts = []
    if a["stash"] > 0:
        parts.append(f"схрон **{kk(a['stash'])}**")
    if a["cash"] > 0:
        parts.append(f"нал **{kk(a['cash'])}**")
    if a["ppk"]:
        parts.append(f"**${a['ppk']:.3f}/кк**")
    if a["mail"] == "с":
        parts.append("почта ✓")
    elif a["mail"] == "без":
        parts.append("⚠ без почты или смена почты")
    return " · ".join(parts)


def rub(p):
    return f"{round(p):,}".replace(",", " ") + " ₽"


def usd(lot):
    v = (lot.get("prices") or {}).get("usd")
    return f"${v:,.2f}".replace(",", " ") if v else ""


def price(lot):  # «16 622 ₽ · $200.85»
    return f"**{rub(lot['price'])}**" + (f" · {usd(lot)}" if usd(lot) else "")


def change(old, new):
    pct = f" ({(new['price'] - old['price']) / old['price'] * 100:+.0f}%)" if old["price"] else ""
    line = f"{rub(old['price'])} → **{rub(new['price'])}**{pct}"
    if usd(old) and usd(new):
        line += f"\n{usd(old)} → **{usd(new)}**"
    return line


def price_changed(a, b):
    """Изменил ли продавец цену. Если поменялась только часть валют, это просто курс."""
    pa, pb = a.get("prices") or {"rub": a["price"]}, b.get("prices") or {"rub": b["price"]}
    common = [c for c in pa if pa.get(c) and pb.get(c)]
    return bool(common) and all(abs(pa[c] - pb[c]) > pa[c] * 0.001 for c in common)


def same_lot(a, b):  # тот же лот, выставленный заново: совпадают раздел и описание
    return a["category"] == b["category"] and a["title"].lower() == b["title"].lower()


def lot_embed(title, color, lot, line, seller):
    abi = abi_line(lot)
    return {"title": title, "color": color, "url": lot["url"],
            "description": f"**{lot['title'][:300]}**\n{lot['category']}\n" + (f"{abi}\n" if abi else "") + f"\n{line}",
            "footer": {"text": f"FunPay · {seller}"}}


def clean(s):  # описание без эмодзи и лишних пробелов
    return re.sub(r"\s+([,.])", r"\1", re.sub(r"\s+", " ", EMOJI.sub(" ", s))).strip(" ,")


def desc_event(old, new, seller):
    """Продавец поменял описание лота (цена отдельно). Для Arena Breakout видно, как изменились склад и нал."""
    changes = []
    a, b = abi_info(old), abi_info(new)
    if a and b:
        for key, name in (("stash", "Склад"), ("cash", "Нал")):
            if abs(a[key] - b[key]) > 1e-6:
                changes.append((name, kk(a[key]), kk(b[key])))
        if a["ppk"] and b["ppk"] and abs(a["ppk"] - b["ppk"]) > 0.0005:
            changes.append(("$ за кк", f"${a['ppk']:.3f}", f"${b['ppk']:.3f}"))
    md = [f"{n}: {x} → **{y}**" for n, x, y in changes]
    embed = {"title": "📝 Описание изменилось", "color": GREY, "url": new["url"],
             "description": f"{new['category']} · {price(new)}\n\n" + "\n".join(
                 md + [f"\nБыло: {clean(old['title'])[:300]}\nСтало: **{clean(new['title'])[:300]}**"]),
             "footer": {"text": f"FunPay · {seller}"}}
    img = [(f"{n}: {x} → {y}", "fg") for n, x, y in changes] + [("Было: " + clean(old["title"]), "muted")]
    return {"embed": embed, "short": "\n".join(md) or "Текст лота изменился: было и стало на картинке.", "lot": new,
            "img": ("Описание изменилось", GREY, img)}


def board(info, uid, image=False):
    """Табличка «Витрина». С картинкой в тексте остаются только ссылки на лоты, остальное на картинке."""
    lots = sorted(info["lots"].values(), key=lambda l: -l["price"])
    lines = []
    for n, l in enumerate(lots[:20], 1):
        if image:
            short = re.sub(r"\s+", " ", EMOJI.sub(" ", l["title"])).replace("[", "(").replace("]", ")").strip()[:60]
            lines.append(f"{n}. [{short}]({l['url']}) · {price(l)}")
            continue
        abi = abi_line(l)
        lines.append(f"• [{l['title'][:90]}]({l['url']})\n  {l['category']} · {price(l)}"
                     + (f"\n  {abi}" if abi else ""))
    if len(lots) > 20:
        lines.append(f"…и ещё {len(lots) - 20}")
    total = sum(l["price"] for l in lots)
    stats = [] if image else [f"Лотов: **{len(lots)}**" + (f" на **{rub(total)}**" if lots else "")]
    if info["rating"] and not image:
        stats.append(f"Рейтинг: **{info['rating']}** из 5, отзывов: **{info['review_count']}**")
    stats.append(f"Проверено: <t:{int(time.time())}:R>")
    embed = {"title": f"🛒 Витрина {info['name']}", "url": f"https://funpay.com/users/{uid}/", "color": GREEN,
             "description": ("\n".join(lines) if lines else "Сейчас ничего не выставлено.") + "\n\n" + "\n".join(stats),
             "footer": {"text": "Обновляется при каждой проверке"}}
    if image:
        embed["image"] = {"url": "attachment://vitrina.png"}
    return embed


# --- Картинки: витрина, события, рынок (нужен Pillow; без него всё приходит текстом) ---
EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation", "C:/Windows/Fonts"]
FONT_NAMES = {False: ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf"],
              True: ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"]}
BG, CARD, FG, MUTED = (30, 31, 34), (43, 45, 49), (242, 243, 245), (148, 155, 164)
C_GREEN, C_BLUE, C_RED, C_CHIP = (87, 242, 135), (88, 101, 242), (237, 66, 69), (56, 58, 64)
C_YELLOW, C_TILE = (254, 231, 92), (35, 36, 40)
ACCENT = {GREEN: C_GREEN, RED: C_RED, YELLOW: C_YELLOW, BLUE: C_BLUE, GREY: MUTED}
W, P, PAD = 800, 28, 20  # ширина картинки, поля, отступ внутри карточки


def _font(size, bold=False):
    from PIL import ImageFont
    for d in FONT_DIRS:
        for n in FONT_NAMES[bold]:
            if os.path.exists(os.path.join(d, n)):
                return ImageFont.truetype(os.path.join(d, n), size)
    raise RuntimeError("не нашёл шрифт с кириллицей")


def _fonts():
    """Шрифты для картинок. None, если Pillow или шрифта нет: тогда сообщения уходят текстом."""
    try:
        import PIL  # noqa: F401
        return {"head": _font(36, True), "sub": _font(21), "title": _font(23, True), "cat": _font(19),
                "chip": _font(19, True), "price": _font(31, True), "usd": _font(22, True), "foot": _font(17),
                "event": _font(24, True), "line": _font(21), "label": _font(18), "value": _font(30, True),
                "section": _font(26, True)}
    except Exception as e:
        print("FunPay: картинку не рисую,", e)
        return None


def _probe():
    from PIL import Image, ImageDraw
    return ImageDraw.Draw(Image.new("RGB", (1, 1)))


def _wrap(draw, s, font, width, max_lines):
    lines, cur = [], ""
    for w in s.split():
        if draw.textlength((cur + " " + w).strip(), font=font) <= width:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    lines = [l for l in lines if l]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def _avatar(url, size):
    from PIL import Image, ImageDraw
    import io
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        img = Image.open(io.BytesIO(r.read())).convert("RGB").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    return img, mask


def _new(H):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


def _header(img, d, F, name, avatar, subtitle, sub_color=MUTED, sub_font="sub"):
    """Шапка: аватарка, имя, строка под ним. Высота 120."""
    x0 = P
    if avatar:
        try:
            av, mask = _avatar(avatar, 72)
            img.paste(av, (P, 26), mask)
            x0 = P + 72 + 18
        except Exception as e:
            print("FunPay: аватарка не загрузилась,", e)
    d.text((x0, 24), name, font=F["head"], fill=FG)
    d.text((x0, 72), subtitle, font=F[sub_font], fill=sub_color)
    return 120


def _footer(d, F, H, extra="funpay.com"):
    stamp = time.strftime("%d.%m %H:%M", time.gmtime(time.time() + 3 * 3600))
    d.text((P, H - 38), f"Обновлено {stamp} МСК   ·   {extra}", font=F["foot"], fill=MUTED)


def _png(img):
    import io
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _lot_card(probe, F, lot):
    """Считает разметку карточки лота: описание, раздел, цена в ₽ и $ справа, плашки Arena Breakout."""
    price_, dollars = rub(lot["price"]), usd(lot)
    pw = max(probe.textlength(price_, font=F["price"]), probe.textlength(dollars, font=F["usd"]) if dollars else 0)
    tl = _wrap(probe, clean(lot["title"]), F["title"], W - 2 * P - 3 * PAD - pw, 3)
    a, chips = abi_info(lot), []
    if a:
        if a["stash"] > 0:
            chips.append((f"Схрон {kk(a['stash'])}", C_CHIP))
        if a["cash"] > 0:
            chips.append((f"Нал {kk(a['cash'])}", C_CHIP))
        if a["ppk"]:
            chips.append((f"${a['ppk']:.3f} за кк", C_BLUE))
        if a["mail"] == "с":
            chips.append(("Почта передаётся", C_CHIP))
        elif a["mail"] == "без":
            chips.append(("Без почты или смена почты", C_RED))
    rows, x = [[]], 0  # плашки переносим на новую строку, если не влезают
    for c in chips:
        cw = probe.textlength(c[0], font=F["chip"]) + 24
        if rows[-1] and x + cw > W - 2 * P - 2 * PAD - 6:
            rows.append([])
            x = 0
        rows[-1].append(c)
        x += cw + 10
    rows = [r for r in rows if r]
    h = PAD + len(tl) * 30 + 6 + 24 + (14 + len(rows) * 42 - 8 if rows else 0) + PAD
    return {"lot": lot, "price": price_, "usd": dollars, "tl": tl, "rows": rows, "h": max(h, PAD + 36 + 30 + PAD)}


def _draw_lot_card(d, F, c, y, accent=C_GREEN):
    h = c["h"]
    d.rounded_rectangle((P, y, W - P, y + h), 14, fill=CARD)
    d.rectangle((P, y + 10, P + 4, y + h - 10), fill=accent)
    d.text((W - P - PAD - d.textlength(c["price"], font=F["price"]), y + PAD - 2), c["price"], font=F["price"], fill=C_GREEN)
    if c["usd"]:
        d.text((W - P - PAD - d.textlength(c["usd"], font=F["usd"]), y + PAD + 38), c["usd"], font=F["usd"], fill=MUTED)
    ty = y + PAD
    for line in c["tl"]:
        d.text((P + PAD + 6, ty), line, font=F["title"], fill=FG)
        ty += 30
    d.text((P + PAD + 6, ty + 6), c["lot"]["category"], font=F["cat"], fill=MUTED)
    cy = ty + 6 + 24 + 14
    for row in c["rows"]:
        cx = P + PAD + 6
        for text_, color in row:
            tw = d.textlength(text_, font=F["chip"])
            d.rounded_rectangle((cx, cy, cx + tw + 24, cy + 34), 17, fill=color)
            d.text((cx + 12, cy + 6), text_, font=F["chip"], fill=FG)
            cx += tw + 24 + 10
        cy += 42
    return y + h


def board_image(info, max_lots=8):
    """PNG с витриной продавца. None, если Pillow или шрифта нет."""
    F = _fonts()
    if not F:
        return None
    probe = _probe()
    lots = sorted(info["lots"].values(), key=lambda l: -l["price"])
    cards = [_lot_card(probe, F, l) for l in lots[:max_lots]]
    more = len(lots) - len(cards)
    H = 120 + (sum(c["h"] + 14 for c in cards) or 90) + (34 if more > 0 else 0) + 50
    img, d = _new(H)
    total = sum(l["price"] for l in lots)
    sub = [f"Лотов: {len(lots)}" + (f" на {rub(total)}" if lots else "")]
    if info.get("rating"):
        sub.append(f"Рейтинг {info['rating']} из 5, отзывов {info['review_count']}")
    y = _header(img, d, F, info["name"], info.get("avatar"), "   ·   ".join(sub))
    if not cards:
        d.rounded_rectangle((P, y, W - P, y + 76), 14, fill=CARD)
        d.text((P + PAD, y + 24), "Сейчас ничего не выставлено", font=F["title"], fill=MUTED)
    for c in cards:
        y = _draw_lot_card(d, F, c, y) + 14
    if more > 0:
        d.text((P, y), f"…и ещё {more}", font=F["sub"], fill=MUTED)
    _footer(d, F, H)
    return _png(img)


def event_image(info, lot, title, color, lines):
    """Картинка события: шапка продавца с названием события, карточка лота, строки «было → стало».
    lines: [(текст, «muted» или «accent» или «fg»), ...]"""
    F = _fonts()
    if not F:
        return None
    accent = ACCENT.get(color, MUTED)
    probe = _probe()
    card = _lot_card(probe, F, lot)
    wrapped = []  # длинные строки (было/стало описания) переносим
    for text_, tone in lines:
        for i, part in enumerate(_wrap(probe, text_, F["line"], W - 2 * P - 2 * PAD - 6, 4) or [""]):
            wrapped.append((part, tone, i == 0))
    box_h = (PAD + sum(30 if first else 27 for _, _, first in wrapped) + PAD - 4) if wrapped else 0
    H = 120 + card["h"] + (14 + box_h if wrapped else 0) + 50
    img, d = _new(H)
    y = _header(img, d, F, info["name"], info.get("avatar"), title, accent, "event")
    y = _draw_lot_card(d, F, card, y, accent) + 14
    if wrapped:
        d.rounded_rectangle((P, y, W - P, y + box_h), 14, fill=CARD)
        ty = y + PAD - 2
        for part, tone, first in wrapped:
            fill = {"accent": accent, "fg": FG}.get(tone, MUTED)
            d.text((P + PAD + 6, ty + (0 if first else -3)), part, font=F["line"], fill=fill)
            ty += 30 if first else 27
    _footer(d, F, H)
    return _png(img)


def _strip(d, F, x0, x1, y, st):
    """Полоска цен за кк: точки соседей, серая зона «обычно», белая черта медиана, зелёный маркер его лот."""
    lo = min(st["ppks"][0], st["ppk"])
    hi = max(min(st["ppks"][-1], st["q3"] + 1.5 * (st["q3"] - st["q1"])), st["ppk"])  # дорогие выбросы обрезаем
    if hi - lo < 1e-9:
        hi = lo + 0.01
    pos = lambda v: x0 + (min(max(v, lo), hi) - lo) / (hi - lo) * (x1 - x0)
    d.rounded_rectangle((pos(st["q1"]), y - 9, pos(st["q3"]), y + 9), 9, fill=C_CHIP)
    d.line((x0, y, x1, y), fill=(70, 72, 78), width=2)
    for v in st["ppks"]:
        cx = pos(v)
        d.ellipse((cx - 4, y - 4, cx + 4, y + 4), fill=MUTED if v <= hi else (110, 112, 118))
    mx = pos(st["med"])
    d.line((mx, y - 16, mx, y + 16), fill=FG, width=3)
    hx = pos(st["ppk"])
    d.ellipse((hx - 9, y - 9, hx + 9, y + 9), fill=C_GREEN, outline=BG, width=2)
    lab = lambda v: f"${v:.2f}"
    d.text((x0, y + 18), lab(lo), font=F["label"], fill=MUTED)
    d.text((x1 - d.textlength(lab(hi) + "+", font=F["label"]), y + 18), lab(hi) + ("+" if st["ppks"][-1] > hi else ""),
           font=F["label"], fill=MUTED)
    mid = f"медиана ${st['med']:.3f}"
    d.text((min(max(mx - d.textlength(mid, font=F["label"]) / 2, x0 + 70), x1 - 70 - d.textlength(mid, font=F["label"])),
            y - 40), mid, font=F["label"], fill=FG)


def market_image(st):
    """Картинка рынка для одного лота. По одной на лот: длинную общую Discord ужимает до нечитаемой."""
    F = _fonts()
    if not F:
        return None
    SEC = 494
    H = P + SEC + 50
    img, d = _new(H)
    y = P
    inner0, inner1 = P + PAD + 6, W - P - PAD
    if True:
        d.rounded_rectangle((P, y, W - P, y + SEC), 14, fill=CARD)
        d.rectangle((P, y + 10, P + 4, y + SEC - 10), fill=C_BLUE)
        d.text((inner0, y + PAD - 2), f"Рынок рядом с лотом {kk(st['s'])} за {usd(st['lot'])}", font=F["section"], fill=FG)
        d.text((inner0, y + PAD + 34), f"{rub(st['lot']['price'])}  ·  ${st['ppk']:.3f} за кк", font=F["line"], fill=MUTED)
        # три плитки: средняя, справедливая цена, где он относительно рынка
        ty, tw, th = y + PAD + 74, (inner1 - inner0 - 24) / 3, 92
        cheaper = st["diff"] < 0
        tiles = [("Средняя за кк", f"${st['med']:.3f}", FG, "медиана соседей"),
                 ("Справедливая цена", f"${st['fair']:.0f}", FG, f"~{rub(st['fair_rub'])}" if st["fair_rub"] else ""),
                 ("Этот лот", f"{'−' if cheaper else '+'}{abs(st['diff']):.0f}%", C_GREEN if cheaper else C_RED,
                  "дешевле рынка" if cheaper else "дороже рынка")]
        for i, (label, value, color, note) in enumerate(tiles):
            tx = inner0 + i * (tw + 12)
            d.rounded_rectangle((tx, ty, tx + tw, ty + th), 12, fill=C_TILE)
            d.text((tx + 14, ty + 10), label, font=F["label"], fill=MUTED)
            d.text((tx + 14, ty + 32), value, font=F["value"], fill=color)
            d.text((tx + 14, ty + 66), note, font=F["label"], fill=MUTED)
        # полоска распределения цен
        _strip(d, F, inner0 + 10, inner1 - 10, ty + th + 70, st)
        sy = ty + th + 118
        note = (f"Соседей {st['n']} (склад от {kk(st['lo_kk'])} до {kk(st['hi_kk'])}), обычно от ${st['q1']:.3f} "
                f"до ${st['q3']:.3f} за кк. Дешевле него {st['cheaper']} из {st['n']}.")
        for k, part in enumerate(_wrap(d, note, F["label"], inner1 - inner0, 2)):
            d.text((inner0, sy + k * 24), part, font=F["label"], fill=MUTED)
        d.text((inner0, sy + 56), "Самые выгодные рядом:", font=F["chip"], fill=FG)
        for k, r in enumerate(st["cheapest"]):
            d.text((inner0, sy + 84 + k * 27), f"{k + 1}.  {kk(r['stash'])} за ${r['usd']:.0f}   ·   ${r['ppk']:.3f} за кк",
                   font=F["line"], fill=FG)
    _footer(d, F, H, f"Arena Breakout (PC), соседи ±{SEGMENT_KK}кк, без «без почты»")
    return _png(img)


def send(webhook, payload, message_id=None, files=None):
    """Отправляет сообщение или переписывает старое. Возвращает id сообщения, None если старого уже нет.
    files: {"имя.png": байты}, тогда сообщение уходит с вложениями."""
    url = f"{webhook}/messages/{message_id}" if message_id else f"{webhook}?wait=true"
    if files:
        boundary = f"----funpay{int(time.time() * 1000)}"
        payload = dict(payload, attachments=[{"id": i, "filename": n} for i, n in enumerate(files)])
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
                 f'Content-Type: application/json\r\n\r\n{json.dumps(payload)}\r\n'.encode("utf-8")]
        for i, (n, data) in enumerate(files.items()):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files[{i}]"; filename="{n}"\r\n'
                         f'Content-Type: image/png\r\n\r\n'.encode("utf-8") + data + b"\r\n")
        body, ctype = b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"
    else:
        body, ctype = json.dumps(payload).encode("utf-8"), "application/json"
    req = urllib.request.Request(url, data=body, method="PATCH" if message_id else "POST",
                                 headers={"Content-Type": ctype, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            msg = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if message_id and e.code == 404:  # табличку удалили руками: создадим заново
            return None
        raise
    time.sleep(1)
    return msg.get("id", message_id)


# --- Рынок: средняя цена за кк у соседей по складу (раз в 12 часов) ---
MARKET_URL = "https://funpay.com/lots/1650/"  # Аккаунты Arena Breakout, все лоты отдаются одной страницей
MARKET_EVERY = 12 * 3600
SEGMENT_KK = 100  # соседи: склад ±100кк от лота продавца


def market_lots():
    """PC-лоты Arena Breakout со складом: цена в $ и $ за кк, как в «ABI Прайсер»."""
    page = fetch(MARKET_URL, "usd")
    rows = []
    for m in re.finditer(r'<a href="(https://funpay\.com/lots/offer\?id=\d+)" class="tc-item[^"]*"([^>]*)>(.*?)</a>', page, re.S):
        url, attrs, block = m.groups()
        platform = re.search(r'data-f-platform="([^"]*)"', attrs)
        desc = re.search(r'<div class="tc-desc-text">(.*?)</div>', block, re.S)
        cost = re.search(r'class="tc-price" data-s="([\d.]+)"', block)
        if not (platform and "(pc)" in platform.group(1) and desc and cost):
            continue
        desc = text(desc.group(1))
        stash = stash_cash(desc[:2000])[0]
        if stash <= 0 or float(cost.group(1)) <= 0:
            continue
        seller = re.search(r"/users/(\d+)/", block)
        rows.append({"url": url, "seller": seller.group(1) if seller else "", "stash": stash,
                     "usd": float(cost.group(1)), "ppk": float(cost.group(1)) / stash, "mail": mail_status(desc)})
    if len(rows) < 20:
        raise RuntimeError(f"на странице рынка всего {len(rows)} лотов, похоже FunPay её поменял")
    return rows


def market_stats(lot, uid, rows):
    """Соседи лота по складу и средняя цена за кк. None, если соседей меньше трёх."""
    import statistics
    a = abi_info(lot)
    s, ppk = a["stash"], a["ppk"]
    # Без лотов самого продавца и без «без почты» (как галочка по умолчанию в «ABI Прайсер»)
    seg = [r for r in rows if r["seller"] != uid and r["mail"] != "без" and abs(r["stash"] - s) <= SEGMENT_KK]
    if len(seg) < 3:
        return None
    ppks = sorted(r["ppk"] for r in seg)
    med = statistics.median(ppks)
    q1, _, q3 = statistics.quantiles(ppks, n=4)
    to_rub = lot["price"] / lot["prices"]["usd"] if (lot.get("prices") or {}).get("usd") else None
    return {"lot": lot, "s": s, "ppk": ppk, "n": len(seg), "ppks": ppks, "med": med, "q1": q1, "q3": q3,
            "fair": med * s, "fair_rub": med * s * to_rub if to_rub else None, "diff": (ppk / med - 1) * 100,
            "cheaper": sum(p < ppk for p in ppks), "cheapest": sorted(seg, key=lambda r: r["ppk"])[:3],
            "lo_kk": max(0, s - SEGMENT_KK), "hi_kk": s + SEGMENT_KK}


def market_text(st, short=False):
    """Текст по одному лоту. short: только ссылки на самых выгодных соседей, цифры тогда на картинке."""
    head = f"**[{kk(st['s'])} за {usd(st['lot'])}]({st['lot']['url']})** · ${st['ppk']:.3f} за кк"
    links = [f"• [{kk(r['stash'])} за ${r['usd']:.0f}]({r['url']}) · ${r['ppk']:.3f} за кк" for r in st["cheapest"]]
    if short:
        return head + "\nСамые выгодные рядом:\n" + "\n".join(links)
    return "\n".join([
        head, "",
        f"Соседи по складу (от {kk(st['lo_kk'])} до {kk(st['hi_kk'])}): **{st['n']}** лотов",
        f"Средняя цена (медиана): **${st['med']:.3f} за кк**",
        f"Для {kk(st['s'])} это **${st['fair']:.0f}**" + (f" (~{rub(st['fair_rub'])})" if st["fair_rub"] else ""),
        f"Обычно от ${st['q1']:.3f} до ${st['q3']:.3f} за кк",
        f"Этот лот {'дешевле' if st['diff'] < 0 else 'дороже'} медианы на **{abs(st['diff']):.0f}%**, "
        f"дешевле него {st['cheaper']} из {st['n']}", "", "Самые выгодные рядом:", *links])


def market_report(sellers):
    """Сообщение с отчётом по рынку: (payload, files) или None, если у продавцов нет лотов Arena Breakout."""
    rows = market_lots()
    stats, few = [], []
    for uid, saved in sellers.items():
        for lot in sorted(saved.get("lots", {}).values(), key=lambda l: -l["price"]):
            a = abi_info(lot)
            if a and a["stash"] > 0 and a["ppk"]:
                st = market_stats(lot, uid, rows)
                (stats if st else few).append(st or lot)
    if not stats and not few:
        return None
    stats = stats[:6]
    footer = {"text": "Цены объявлений, а не продаж. Без лотов «без почты». Раз в 12 часов"}
    notes = [f"Лот {kk(abi_info(l)['stash'])}: соседей по складу ±{SEGMENT_KK}кк меньше трёх, считать нечего."
             for l in few]
    pngs = [market_image(st) for st in stats]
    if stats and all(pngs):  # по карточке с картинкой на каждый лот
        embeds = [{"title": f"📊 Рынок рядом с лотом {kk(st['s'])}", "url": st["lot"]["url"], "color": BLUE,
                   "description": market_text(st, short=True), "image": {"url": f"attachment://market_{i}.png"}}
                  for i, st in enumerate(stats)]
        embeds[-1]["footer"] = footer
        if notes:
            embeds.append({"title": "📊 Рынок", "color": BLUE, "description": "\n".join(notes)})
        return {"username": "Рынок FunPay", "embeds": embeds}, {f"market_{i}.png": png for i, png in enumerate(pngs)}
    embeds = [{"title": f"📊 Рынок рядом с лотом {kk(st['s'])}", "url": st["lot"]["url"], "color": BLUE,
               "description": market_text(st), "footer": footer} for st in stats]
    if notes:
        embeds.append({"title": "📊 Рынок", "color": BLUE, "description": "\n".join(notes)})
    return {"username": "Рынок FunPay", "embeds": embeds[:10]}, None


def event(title, color, lot, line, seller, img_lines=()):
    """Событие по лоту: текстовая карточка (если картинку не нарисовать) и строки для картинки."""
    return {"embed": lot_embed(title, color, lot, line, seller), "short": line, "lot": lot,
            "img": (EMOJI.sub("", title).strip(), color, list(img_lines))}


def price_lines(old, new):
    pct = (new["price"] / old["price"] - 1) * 100 if old["price"] else 0
    return [(f"Было: {rub(old['price'])}" + (f" · {usd(old)}" if usd(old) else ""), "muted"),
            (f"Стало: {rub(new['price'])}" + (f" · {usd(new)}" if usd(new) else "") + f"   ({pct:+.0f}%)", "accent")]


def check(uid, saved, webhook):
    info = seller_page(uid)
    now = int(time.time())
    seller = info["name"]
    events = []
    if saved is None:  # первый запуск: ничего не объявляем, только запоминаем и показываем витрину
        saved = {"lots": {}, "gone": {}, "review_count": info["review_count"]}
        events.append({"lot": None, "embed": {
            "title": f"👀 Слежу за продавцом {seller}", "color": GREY, "url": f"https://funpay.com/users/{uid}/",
            "description": f"Сейчас выставлено лотов: **{len(info['lots'])}**. Дальше буду писать, "
                           f"когда появится новый лот, лот уйдёт или поменяется цена."}})
        saved["lots"] = info["lots"]
    else:
        old, gone = saved["lots"], saved.setdefault("gone", {})
        new_reviews = info["review_count"] - saved.get("review_count", info["review_count"])
        for lid, lot in info["lots"].items():
            if lid in old:
                if price_changed(old[lid], lot):
                    events.append(event("💲 Цена изменилась", YELLOW, lot, change(old[lid], lot), seller,
                                        price_lines(old[lid], lot)))
                if clean(old[lid]["title"]) != clean(lot["title"]):
                    events.append(desc_event(old[lid], lot, seller))
                continue
            back = next((g for g, v in gone.items() if same_lot(v, lot)), None)
            if back:
                was = gone.pop(back)
                if price_changed(was, lot):
                    events.append(event("🔁 Лот вернулся с другой ценой", BLUE, lot, change(was, lot), seller,
                                        price_lines(was, lot)))
                else:
                    events.append(event("🔁 Лот выставлен заново", BLUE, lot, f"Цена та же: {price(lot)}", seller,
                                        [("Цена та же, что до снятия", "muted")]))
            else:
                events.append(event("🆕 Новый лот", GREEN, lot, f"Цена: {price(lot)}", seller))
        for lid, lot in old.items():
            if lid in info["lots"]:
                continue
            line = f"Стоил: {price(lot)}"
            img = [(f"Стоил: {rub(lot['price'])}" + (f" · {usd(lot)}" if usd(lot) else ""), "muted")]
            if new_reviews > 0 and info["reviews"]:  # одновременно пришёл отзыв: похоже на продажу
                line += "\nВ это же время появился новый отзыв, похоже, продан:\n> " + info["reviews"][0][:200]
                img += [("Похоже, продан: в это же время пришёл новый отзыв", "accent"), (info["reviews"][0][:200], "fg")]
            events.append(event("❌ Лот снят или продан", RED, lot, line, seller, img))
            gone[lid] = dict(lot, gone_at=now)
        for lid in [g for g, v in gone.items() if now - v["gone_at"] > KEEP_GONE_DAYS * 86400]:
            del gone[lid]
        saved["lots"] = info["lots"]
        saved["review_count"] = info["review_count"]
    who = {"username": seller}  # сообщения подписаны именем и аватаркой продавца
    if info.get("avatar"):
        who["avatar_url"] = info["avatar"]
    for e in events:
        print(f"{seller}: {e['embed']['title']}")
        if not webhook:
            continue
        embed, files = dict(e["embed"]), None
        png = event_image(info, e["lot"], *e["img"]) if e["lot"] else None
        if png:  # лот и цифры на картинке, в тексте остаётся только суть события
            embed["description"] = e["short"]
            embed["image"] = {"url": "attachment://lot.png"}
            files = {"lot.png": png}
        send(webhook, dict(who, embeds=[embed]), files=files)
    if webhook:
        if saved.get("board_style") != "image":  # старую текстовую табличку от «FunPay» не трогаем, ставим новую
            saved.pop("board_id", None)
            saved["board_style"] = "image"
        png = board_image(info)
        files = {"vitrina.png": png} if png else None
        payload = dict(who, embeds=[board(info, uid, image=bool(png))])
        board_id = send(webhook, payload, saved.get("board_id"), files) if saved.get("board_id") else None
        saved["board_id"] = board_id or send(webhook, payload, files=files)
    print(f"{seller}: лотов {len(info['lots'])}, событий {len(events)}")
    return saved


def main():
    webhook = os.environ.get("DISCORD_FUNPAY", "").strip()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    sellers = state.setdefault("sellers", {})
    for uid in SELLERS:
        try:
            result = check(uid, sellers.get(uid), webhook)
        except Exception as e:
            print(f"FunPay {uid}: ошибка {e}")
            continue
        if webhook:  # без вебхука ничего не запоминаем, чтобы настоящий запуск начал с чистого листа
            sellers[uid] = result
    due = time.time() - state.get("market_at", 0) >= MARKET_EVERY - 1800  # запуски GitHub плавают
    if webhook and (due or state.get("market_style") != "image2"):  # первый отчёт в новом виде шлём сразу
        try:
            report = market_report(sellers)
            if report:
                send(webhook, report[0], files=report[1])
            state["market_at"] = int(time.time())
            state["market_style"] = "image2"
            print("FunPay: отчёт по рынку", "с картинкой" if report and report[1] else "текстом")
        except Exception as e:
            print("FunPay: рынок не посчитал,", e)
    if webhook:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # FunPay не должен ронять проверку кодов
        print("FunPay: ошибка", e)
        sys.exit(0)
