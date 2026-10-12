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


# --- Картинка витрины (нужен Pillow; без него витрина приходит просто текстом) ---
EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation", "C:/Windows/Fonts"]
FONT_NAMES = {False: ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf"],
              True: ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"]}
BG, CARD, FG, MUTED = (30, 31, 34), (43, 45, 49), (242, 243, 245), (148, 155, 164)
C_GREEN, C_BLUE, C_RED, C_CHIP = (87, 242, 135), (88, 101, 242), (237, 66, 69), (56, 58, 64)


def _font(size, bold=False):
    from PIL import ImageFont
    for d in FONT_DIRS:
        for n in FONT_NAMES[bold]:
            if os.path.exists(os.path.join(d, n)):
                return ImageFont.truetype(os.path.join(d, n), size)
    raise RuntimeError("не нашёл шрифт с кириллицей")


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


def board_image(info, max_lots=8):
    """PNG с витриной продавца. None, если Pillow или шрифта нет."""
    try:
        from PIL import Image, ImageDraw
        import io
        f_head, f_sub, f_title = _font(36, True), _font(21), _font(23, True)
        f_cat, f_chip, f_price, f_foot = _font(19), _font(19, True), _font(31, True), _font(17)
        f_usd = _font(22, True)
    except Exception as e:
        print("FunPay: картинку не рисую,", e)
        return None
    W, P, PAD = 800, 28, 20
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lots = sorted(info["lots"].values(), key=lambda l: -l["price"])
    cards = []  # заранее считаем высоту каждой карточки
    for l in lots[:max_lots]:
        price, dollars = rub(l["price"]), usd(l)
        pw = max(probe.textlength(price, font=f_price), probe.textlength(dollars, font=f_usd) if dollars else 0)
        title = re.sub(r"\s+([,.])", r"\1", re.sub(r"\s+", " ", EMOJI.sub(" ", l["title"]))).strip(" ,")
        tl = _wrap(probe, title, f_title, W - 2 * P - 3 * PAD - pw, 3)
        a, chips = abi_info(l), []
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
            cw = probe.textlength(c[0], font=f_chip) + 24
            if rows[-1] and x + cw > W - 2 * P - 2 * PAD - 6:
                rows.append([])
                x = 0
            rows[-1].append(c)
            x += cw + 10
        rows = [r for r in rows if r]
        h = PAD + len(tl) * 30 + 6 + 24 + (14 + len(rows) * 42 - 8 if rows else 0) + PAD
        cards.append((l, price, dollars, pw, tl, rows, max(h, PAD + 36 + 30 + PAD)))
    head_h = 120
    body_h = sum(c[-1] + 14 for c in cards) or 90
    more = len(lots) - len(cards)
    H = head_h + body_h + (34 if more > 0 else 0) + 50
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # шапка: аватарка, имя, рейтинг
    x0 = P
    if info.get("avatar"):
        try:
            av, mask = _avatar(info["avatar"], 72)
            img.paste(av, (P, 26), mask)
            x0 = P + 72 + 18
        except Exception as e:
            print("FunPay: аватарка не загрузилась,", e)
    d.text((x0, 26), info["name"], font=f_head, fill=FG)
    total = sum(l["price"] for l in lots)
    sub = [f"Лотов: {len(lots)}" + (f" на {rub(total)}" if lots else "")]
    if info.get("rating"):
        sub.append(f"Рейтинг {info['rating']} из 5, отзывов {info['review_count']}")
    d.text((x0, 72), "   ·   ".join(sub), font=f_sub, fill=MUTED)
    y = head_h
    if not cards:
        d.rounded_rectangle((P, y, W - P, y + 76), 14, fill=CARD)
        d.text((P + PAD, y + 24), "Сейчас ничего не выставлено", font=f_title, fill=MUTED)
    for l, price, dollars, pw, tl, rows, h in cards:
        d.rounded_rectangle((P, y, W - P, y + h), 14, fill=CARD)
        d.rectangle((P, y + 10, P + 4, y + h - 10), fill=C_GREEN)
        d.text((W - P - PAD - d.textlength(price, font=f_price), y + PAD - 2), price, font=f_price, fill=C_GREEN)
        if dollars:
            d.text((W - P - PAD - d.textlength(dollars, font=f_usd), y + PAD + 38), dollars, font=f_usd, fill=MUTED)
        ty = y + PAD
        for line in tl:
            d.text((P + PAD + 6, ty), line, font=f_title, fill=FG)
            ty += 30
        d.text((P + PAD + 6, ty + 6), l["category"], font=f_cat, fill=MUTED)
        cy = ty + 6 + 24 + 14
        for row in rows:
            cx = P + PAD + 6
            for text_, color in row:
                tw = d.textlength(text_, font=f_chip)
                d.rounded_rectangle((cx, cy, cx + tw + 24, cy + 34), 17, fill=color)
                d.text((cx + 12, cy + 6), text_, font=f_chip, fill=FG)
                cx += tw + 24 + 10
            cy += 42
        y += h + 14
    if more > 0:
        d.text((P, y), f"…и ещё {more}", font=f_sub, fill=MUTED)
        y += 34
    stamp = time.strftime("%d.%m %H:%M", time.gmtime(time.time() + 3 * 3600))
    d.text((P, H - 38), f"Обновлено {stamp} МСК   ·   funpay.com", font=f_foot, fill=MUTED)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


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


def check(uid, saved, webhook):
    info = seller_page(uid)
    now = int(time.time())
    seller = info["name"]
    events = []
    if saved is None:  # первый запуск: ничего не объявляем, только запоминаем и показываем витрину
        saved = {"lots": {}, "gone": {}, "review_count": info["review_count"]}
        events.append({"title": f"👀 Слежу за продавцом {seller}", "color": GREY,
                       "url": f"https://funpay.com/users/{uid}/",
                       "description": f"Сейчас выставлено лотов: **{len(info['lots'])}**. Дальше буду писать, "
                                      f"когда появится новый лот, лот уйдёт или поменяется цена."})
        saved["lots"] = info["lots"]
    else:
        old, gone = saved["lots"], saved.setdefault("gone", {})
        new_reviews = info["review_count"] - saved.get("review_count", info["review_count"])
        for lid, lot in info["lots"].items():
            if lid in old:
                if price_changed(old[lid], lot):
                    events.append(lot_embed("💲 Цена изменилась", YELLOW, lot, change(old[lid], lot), seller))
                continue
            back = next((g for g, v in gone.items() if same_lot(v, lot)), None)
            if back:
                was = gone.pop(back)
                if price_changed(was, lot):
                    events.append(lot_embed("🔁 Лот вернулся с другой ценой", BLUE, lot, change(was, lot), seller))
                else:
                    events.append(lot_embed("🔁 Лот выставлен заново", BLUE, lot, f"Цена та же: {price(lot)}", seller))
            else:
                events.append(lot_embed("🆕 Новый лот", GREEN, lot, f"Цена: {price(lot)}", seller))
        for lid, lot in old.items():
            if lid in info["lots"]:
                continue
            line = f"Стоил: {price(lot)}"
            if new_reviews > 0 and info["reviews"]:  # одновременно пришёл отзыв: похоже на продажу
                line += "\nВ это же время появился новый отзыв, похоже, продан:\n> " + info["reviews"][0][:200]
            events.append(lot_embed("❌ Лот снят или продан", RED, lot, line, seller))
            gone[lid] = dict(lot, gone_at=now)
        for lid in [g for g, v in gone.items() if now - v["gone_at"] > KEEP_GONE_DAYS * 86400]:
            del gone[lid]
        saved["lots"] = info["lots"]
        saved["review_count"] = info["review_count"]
    who = {"username": seller}  # сообщения подписаны именем и аватаркой продавца
    if info.get("avatar"):
        who["avatar_url"] = info["avatar"]
    for e in events:
        print(f"{seller}: {e['title']}")
        if webhook:
            send(webhook, dict(who, embeds=[e]))
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
