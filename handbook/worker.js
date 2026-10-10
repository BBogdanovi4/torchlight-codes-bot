// Справочник Torchlight: Infinite для Discord: команды /skill и /item.
// Работает на Cloudflare Workers. Данные: список из handbook/index.json (собирается раз в неделю)
// и подсказки с tlidb.com, которые бот берёт в момент запроса.
// Переменная окружения: DISCORD_PUBLIC_KEY (Discord Developer Portal > General Information > Public Key).

const INDEX_URL = "https://raw.githubusercontent.com/BBogdanovi4/torchlight-codes-bot/main/handbook/index.json";
const TLIDB = "https://tlidb.com";
const DISPATCH_URL = "https://api.github.com/repos/BBogdanovi4/torchlight-codes-bot/actions/workflows/check-codes.yml/dispatches";
const COLORS = { ItemGold: 0xe6a23c, Pactspirit: 0x9b59b6, skill: 0x3498db, item: 0x95a5a6 };

let indexCache = { at: 0, data: null };

async function loadIndex() {
  if (indexCache.data && Date.now() - indexCache.at < 3600_000) return indexCache.data;
  const r = await fetch(INDEX_URL, { cf: { cacheTtl: 3600 } });
  if (!r.ok) throw new Error(`index.json: HTTP ${r.status}`);
  indexCache = { at: Date.now(), data: await r.json() };
  return indexCache.data;
}

export const norm = s => s.toLowerCase().replace(/ё/g, "е").replace(/[^\p{L}\p{N}]+/gu, " ").trim();

// entry: [ссылка, имя по-русски, имя по-английски, путь к подсказке, категория]
export function search(entries, query, limit = 25) {
  const q = norm(query || "");
  if (!q) return entries.slice(0, limit);
  const scored = [];
  for (const e of entries) {
    let best = 9;
    for (const name of [norm(e[1]), norm(e[2]), norm(e[0])]) {
      let s = 9;
      if (name === q) s = 0;
      else if (name.startsWith(q)) s = 1;
      else if (name.split(" ").some(w => w.startsWith(q))) s = 2;
      else if (name.includes(q)) s = 3;
      best = Math.min(best, s);
    }
    if (best < 9) scored.push([best, e[1].length, e]);
  }
  scored.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return scored.slice(0, limit).map(x => x[2]);
}

const decode = s => s
  .replace(/&nbsp;/g, " ").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
  .replace(/&#39;/g, "'").replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n)).replace(/&amp;/g, "&");
const text = s => decode(s.replace(/<[^>]+>/g, "")).replace(/\s+/g, " ").trim();

// Превращает HTML-подсказку tlidb в поля для карточки Discord.
export function parseTooltip(html) {
  html = html.replace(/<!--[\s\S]*?-->/g, "");
  // У питомцев (Pactspirit) название в div с цветом редкости, а картинка фоном, а не тегом img.
  const title = text((html.match(/<h5[^>]*card-title[^>]*>([\s\S]*?)<\/h5>/) ||
                      html.match(/<div class="text-center item_rarity\d*">([\s\S]*?)<\/div>/) || [, ""])[1]);
  const icon = (html.match(/class="[^"]*\bicon pactspirit[^"]*" style="background-image: url\('([^']+)'\)/) ||
                html.match(/<img[^>]+src="([^"]+)"/) || [, null])[1];
  const level = (html.match(/<div class="level">\s*(\d+)\s*<\/div>/) || [, null])[1];
  const tags = [...html.matchAll(/<span class="[^"]*(?:\btag\b|btn-secondary)[^"]*">([\s\S]*?)<\/span>/g)].map(m => text(m[1])).filter(Boolean);

  let body = html.split(/<div class="px-3 pt-1 pb-3">/)[1] || "";
  body = body.split(/<div[^>]*Func_SkillTips_DetailMode[^>]*>/)[0];        // подробный режим повторяет краткий
  body = body.replace(/<div[^>]*class="detailsNote"[^>]*>[\s\S]*?<\/div>/g, "");
  body = body.replace(/<div class="d-flex justify-content-center">\s*<div>([\s\S]*?)<\/div>\s*<div class="ps-2">([\s\S]*?)<\/div>\s*<\/div>/g,
    (_, k, v) => `\n${text(k).replace(/:$/, "")}: **${text(v)}**\n`);
  body = body.replace(/<div style="color: gold[^"]*">([\s\S]*?)<\/div>/g, (_, h) => `\n**${text(h)}**\n`);
  body = body.replace(/<span class="text-mod">([\s\S]*?)<\/span>/g, (_, v) => `**${text(v)}**`);
  body = body.replace(/<div[^>]*fst-italic[^>]*>([\s\S]*?)<\/div>/g,
    (_, t) => "\n" + t.split(/<br\s*\/?>/).map(l => text(l)).filter(Boolean).map(l => `*${l}*`).join("\n") + "\n");
  body = body.replace(/<br\s*\/?>/g, "\n").replace(/<hr[^>]*>/g, "\n").replace(/<\/div>/g, "\n");
  body = decode(body.replace(/<[^>]+>/g, ""));
  body = body.replace(/([а-яё])([А-ЯЁ])/g, "$1 $2"); // на tlidb слова рядом с иконками слипаются: «ЗаклинаниеОгонь»
  const lines = body.split("\n").map(l => l.replace(/[ \t]+/g, " ").trim()).filter(Boolean);
  return { title, icon, level, tags, body: lines.join("\n") };
}

export function buildEmbed(entry, kind, tip) {
  const hoverType = (entry[3].split("/")[3] || "").replace("Torchlight_", "").replace("_hover", "");
  const head = [];
  if (tip.tags.length) head.push(tip.tags.join(" · "));
  if (tip.level && kind === "skill") head.push(`Уровень навыка: ${tip.level}`);
  let description = [head.join("\n"), tip.body].filter(Boolean).join("\n\n");
  if (description.length > 3900) description = description.slice(0, 3900) + "…";
  const embed = {
    title: tip.title || entry[1],
    url: `${TLIDB}/ru/${entry[0]}`,
    description,
    color: COLORS[hoverType] || COLORS[kind],
    footer: { text: `${entry[2]} · данные tlidb.com` },
  };
  if (tip.icon) embed.thumbnail = { url: tip.icon };
  return embed;
}

async function verify(request, body, publicKey) {
  const sig = request.headers.get("X-Signature-Ed25519");
  const ts = request.headers.get("X-Signature-Timestamp");
  if (!sig || !ts || !publicKey) return false;
  const hex = h => new Uint8Array(h.match(/.{2}/g).map(b => parseInt(b, 16)));
  try {
    const key = await crypto.subtle.importKey("raw", hex(publicKey), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify("Ed25519", key, hex(sig), new TextEncoder().encode(ts + body));
  } catch {
    return false;
  }
}

const json = obj => new Response(JSON.stringify(obj), { headers: { "Content-Type": "application/json" } });

async function answer(interaction) {
  const kind = interaction.data.name; // skill или item
  const query = (interaction.data.options || [])[0]?.value || "";
  const index = await loadIndex();
  const entries = index[kind] || [];
  const entry = entries.find(e => e[0] === query) || search(entries, query, 1)[0];
  if (!entry) return { content: `Не нашёл «${query}». Начни вводить название и выбери из подсказок.` };
  const r = await fetch(TLIDB + entry[3], { cf: { cacheTtl: 86400 } });
  if (!r.ok) return { content: `tlidb.com сейчас не отвечает. Страница: ${TLIDB}/ru/${entry[0]}` };
  return { embeds: [buildEmbed(entry, kind, parseTooltip(await r.text()))] };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("Справочник Torchlight: Infinite работает.");
    const body = await request.text();
    if (!(await verify(request, body, env.DISCORD_PUBLIC_KEY))) return new Response("Bad signature", { status: 401 });
    const interaction = JSON.parse(body);

    if (interaction.type === 1) return json({ type: 1 }); // проверка адреса со стороны Discord

    if (interaction.type === 4) { // подсказки, пока человек печатает
      const kind = interaction.data.name;
      const focused = (interaction.data.options || []).find(o => o.focused)?.value || "";
      const index = await loadIndex();
      const choices = search(index[kind] || [], focused).map(e => ({
        name: (e[1] === e[2] ? e[1] : `${e[1]} · ${e[2]}`).slice(0, 100),
        value: e[0].slice(0, 100),
      }));
      return json({ type: 8, data: { choices } });
    }

    if (interaction.type === 2) { // сама команда: сразу «думает…», карточку дописываем следом
      const url = `https://discord.com/api/v10/webhooks/${interaction.application_id}/${interaction.token}/messages/@original`;
      ctx.waitUntil((async () => {
        let payload;
        try {
          payload = await answer(interaction);
        } catch (e) {
          payload = { content: `Что-то сломалось: ${e.message}` };
        }
        await fetch(url, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      })());
      return json({ type: 5 });
    }

    return new Response("Unknown interaction", { status: 400 });
  },

  // Запасной будильник для бота кодов: GitHub иногда пропускает запуски по расписанию.
  // Cloudflare > Worker > Settings > Triggers > Cron: "37 * * * *". Нужен секрет GITHUB_TOKEN
  // (fine-grained токен только на этот репозиторий, право Actions: Read and write).
  async scheduled(event, env, ctx) {
    if (!env.GITHUB_TOKEN) return;
    ctx.waitUntil(fetch(DISPATCH_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tli-spravochnik",
      },
      body: JSON.stringify({ ref: "main" }),
    }).then(r => { if (!r.ok) console.log("Запуск бота кодов не удался:", r.status); }));
  },
};
