/*
 * Mnemo demo backend that runs in the browser (GitHub Pages has no server).
 *
 * Intercepts the app's fetch("/api/...") calls and answers from window.MNEMO_DEMO (data.js, built by
 * scripts/build_demo.py). Search, templates and navigation work for real on the snapshot; AI answers are
 * scripted from the found notes; anything that would change files is refused politely.
 */
(() => {
  "use strict";
  const D = window.MNEMO_DEMO;
  const realFetch = window.fetch.bind(window);
  // the demo is a showcase: always start on the dashboard (or on #view from the URL, e.g. demo/#graph)
  try { localStorage.setItem("view", location.hash.slice(1) || "dashboard"); } catch { /* storage blocked */ }
  const RO = "Это демо: изменения не сохраняются. Установи Mnemo локально, чтобы работать со своим хранилищем.";
  const DEMO_NOTE = "\n\n*Демо: ответ собран из найденных заметок без ИИ. В установленном Mnemo здесь отвечает модель.*";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const json = (obj, status = 200) =>
    new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
  const fail = (msg, status = 400) => json({ detail: msg }, status);

  function ndjson(events) {
    const enc = new TextEncoder();
    const body = new ReadableStream({
      async start(ctrl) {
        const send = (ev) => ctrl.enqueue(enc.encode(JSON.stringify(ev) + "\n"));
        await sleep(250);
        for (const ev of events) {
          if (ev.type !== "token") { send(ev); continue; }
          // "type" the answer out like a streaming model
          for (let i = 0; i < ev.content.length; i += 7) {
            send({ type: "token", content: ev.content.slice(i, i + 7) });
            await sleep(12);
          }
        }
        send({ type: "done" });
        ctrl.close();
      },
    });
    return new Response(body, { headers: { "Content-Type": "application/x-ndjson" } });
  }

  // ---------------------------------------------------------------- notes & search
  const body = (content) => content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "");
  const NOTES = Object.fromEntries(Object.entries(D.notes).map(([p, n]) => [p, { ...n, text: body(n.content), lower: body(n.content).toLowerCase() }]));
  const STOP = new Set(("где что как когда какой какая какое какие почему зачем кто чем сколько есть ли это эта эти этот про для при над под " +
    "без или все всё мой моя мои мне меня писал писала записывал заметка заметке заметки заметках заметку найди найти напомни вспомни покажи " +
    "расскажи объясни был была было были тоже также очень можно нужно надо там тут вот она они оно его еще ещё уже").split(" "));
  const stem = (t) => (t.length >= 5 ? t.slice(0, Math.max(3, t.length - 2)) : t);

  function search(query, limit = 12, folder = "") {
    const q = query.toLowerCase().trim();
    let tokens = (q.match(/[\p{L}\p{N}_'-]+/gu) || []).filter((t) => t.length >= 3 && !STOP.has(t)).map(stem);
    if (!tokens.length && q) tokens = [q];
    const out = [];
    for (const [p, n] of Object.entries(NOTES)) {
      if (folder && !(n.folder === folder || n.folder.startsWith(folder + "/"))) continue;
      const title = n.title.toLowerCase();
      let score = 0, first = -1;
      for (const t of tokens) {
        let c = 0, i = n.lower.indexOf(t);
        if (i >= 0 && (first < 0 || i < first)) first = i;
        while (i >= 0 && c < 20) { c++; i = n.lower.indexOf(t, i + t.length); }
        score += (title.split(t).length - 1) * 10 + c + n.tags.filter((tag) => tag.includes(t)).length * 5;
      }
      if (q && title.includes(q)) score += 30;
      if (score > 0) {
        const start = Math.max(0, first - 120);
        const snippet = n.text.slice(start, start + 300).replace(/^#+\s*/gm, "").replace(/\s+/g, " ").trim();
        out.push({ path: p, title: n.title, folder: n.folder, score, snippet, tags: n.tags, mtime: n.mtime });
      }
    }
    return out.sort((a, b) => b.score - a.score).slice(0, limit);
  }

  function resolve(target) {
    const t = target.trim().replace(/\.md$/i, "").toLowerCase();
    const byPath = Object.keys(NOTES).find((p) => p.slice(0, -3).toLowerCase() === t);
    if (byPath) return byPath;
    return Object.keys(NOTES).find((p) => p.split("/").pop().slice(0, -3).toLowerCase() === t.split("/").pop()) || null;
  }

  // ---------------------------------------------------------------- templates (same rules as backend/templates.py)
  const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
  const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const pad = (n) => String(n).padStart(2, "0");
  function momentFormat(d, fmt) {
    const gen = /D[oD]?(\[[^\[\]]*\]|\s)+MMMM/.test(fmt);
    return fmt.replace(/\[([^\]]*)\]|YYYY|MMMM|MM|DD|HH|mm/g, (m, lit) => lit !== undefined ? lit : ({
      YYYY: d.getFullYear(), MMMM: (gen ? MONTHS_GEN : MONTHS)[d.getMonth()], MM: pad(d.getMonth() + 1),
      DD: pad(d.getDate()), HH: pad(d.getHours()), mm: pad(d.getMinutes()),
    })[m]);
  }
  function renderTemplate(name, title) {
    const src = D.template_sources[name];
    if (!src) throw new Error(`Шаблон не найден: ${name}`);
    const now = new Date();
    let out = src.replace(/<%[-_]?\*[\s\S]*?[-_]?%>\r?\n?/g, "");
    out = out.replace(/<%[-_]?\s*([\s\S]*?)\s*[-_]?%>/g, (m, code) => {
      if (code === "tp.file.title") return title;
      const d = code.match(/^tp\.date\.now\(\s*["']([^"']*)["']/);
      if (d || code.startsWith("tp.date.now")) return momentFormat(now, d ? d[1] : "YYYY-MM-DD");
      return "";
    });
    return out.trimStart().startsWith("---") ? out.trimStart() : out;
  }
  function templateFor(value, folder) {
    const items = D.templates.items;
    if (value && value !== "auto") return items.find((t) => t.name === value);
    let best = null;
    for (const [f, t] of Object.entries(D.templates.folder_map)) {
      if ((folder === f || folder.startsWith(f + "/")) && (!best || f.length > best[0].length)) best = [f, t];
    }
    return best ? items.find((t) => t.path === best[1]) : null;
  }
  // put the user's request into the first section of a template, like a (very lazy) model would
  function fillTemplate(text, request) {
    return text.replace(/(^## [^\n]+\n)(>[^\n]*\n)?/m, (m, head) => `${head}${request}\n`);
  }

  // ---------------------------------------------------------------- scripted "AI"
  const link = (h) => `[[${h.title}]]`;
  function recallAnswer(question) {
    const hits = search(question, 4);
    if (!hits.length) return { hits, text: "В заметках ничего похожего не нашлось. Попробуй другие слова." + DEMO_NOTE };
    const [top, ...rest] = hits;
    let text = `Похоже, это в заметке ${link(top)}: ${top.snippet.slice(0, 220)}…`;
    if (rest.length) text += `\n\nЕщё по теме: ${rest.slice(0, 2).map(link).join(", ")}.`;
    return { hits, text: text + DEMO_NOTE };
  }
  function chatAnswer(question) {
    const hits = search(question, 5);
    if (!hits.length) return { hits, text: "Поиск по заметкам ничего не нашёл." + DEMO_NOTE };
    const text = `Вот что есть в твоих заметках по этому вопросу:\n\n` +
      hits.slice(0, 3).map((h) => `- **${link(h)}** — ${h.snippet.slice(0, 160)}…`).join("\n");
    return { hits, text: text + DEMO_NOTE };
  }
  const sourcesEvent = (hits) => ({ type: "sources", method: "keyword", sources: hits.map((h) => ({ path: h.path, title: h.title })) });

  function summarize({ path, folder }) {
    if (path) {
      const n = NOTES[path];
      const heads = (n.text.match(/^##\s+.+$/gm) || []).map((h) => h.replace(/^##\s+/, ""));
      return `**Суть:** ${n.text.replace(/^#.*$/gm, "").replace(/\s+/g, " ").trim().slice(0, 200)}…\n\n` +
        (heads.length ? `**Разделы:** ${heads.join(" · ")}\n\n` : "") +
        `**Связи:** ${n.outlinks.length} исходящих, ${n.backlinks.length} входящих.` + DEMO_NOTE;
    }
    const notes = Object.values(NOTES).filter((n) => !folder || n.folder === folder || n.folder.startsWith(folder + "/"));
    const tags = {};
    notes.forEach((n) => n.tags.forEach((t) => (tags[t] = (tags[t] || 0) + 1)));
    const topTags = Object.entries(tags).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([t]) => "#" + t);
    return `В «${folder || "хранилище"}» ${notes.length} заметок. Основные темы: ${topTags.join(", ")}.\n\n` +
      `Самые связанные: ${notes.sort((a, b) => b.backlinks.length - a.backlinks.length).slice(0, 4).map((n) => `[[${n.title}]]`).join(", ")}.` + DEMO_NOTE;
  }

  function plan(instruction) {
    const low = instruction.toLowerCase();
    const pick = /ошибк|баг|проблем|error/.test(low) ? "Problem Template"
      : /технолог|библиотек|фреймворк/.test(low) ? "Technology Template"
      : /идея|мысль|инбокс|inbox/.test(low) ? "Inbox Template" : "Concept Template";
    const tpl = D.templates.items.find((t) => t.name === pick);
    const quoted = instruction.match(/[«"]([^»"]+)[»"]/);
    const about = instruction.match(/(?:про|о|об)\s+(.+?)(?:[.,;!?]|$)/i);
    let title = (quoted ? quoted[1] : about ? about[1] : "Новая заметка").trim();
    title = title.charAt(0).toUpperCase() + title.slice(1);
    const path = `${tpl.folder}/${title}.md`;
    const related = search(instruction, 2);
    const content = fillTemplate(renderTemplate(pick, title), `Черновик по запросу: ${instruction}` +
      (related.length ? `\n\nСм. также: ${related.map(link).join(", ")}` : ""));
    const actions = [{
      type: "create", path, template: pick, ok: true, error: null, content,
      brief: instruction, summary: `Создать заметку ${path} по шаблону «${pick}»`,
    }];
    if (related[0]) actions.push({
      type: "add_links", path: related[0].path, links: [path], ok: true, error: null,
      summary: `Добавить ссылки в ${related[0].path}: [[${title}]]`,
    });
    return {
      model: "демо",
      explanation: `Демо-агент (без ИИ): выбрал «${pick}» по ключевым словам и положил заметку в «${tpl.folder}». ` +
        "Настоящий агент понимает задачу целиком: раскладывает заметки, ставит теги и связи, заполняет шаблоны.",
      actions,
    };
  }

  function suggest(path) {
    const n = NOTES[path];
    const vaultTags = D.stats.tags.map((t) => t.tag).filter((t) => !n.tags.includes(t));
    const tags = vaultTags.filter((t) => n.lower.includes(stem(t.split("/").pop()))).slice(0, 3);
    const linked = new Set(n.outlinks.map((o) => o.path));
    const cands = search(`${n.title} ${n.tags.join(" ")}`, 8).filter((h) => h.path !== path && !linked.has(h.path));
    return {
      model: "демо", tags: tags.length ? tags : vaultTags.slice(0, 2), links: cands.slice(0, 2), folder: "",
      folder_exists: true, current_folder: n.folder, candidates: cands,
      reason: "Демо: теги и связи подобраны по совпадению слов. Настоящая модель подбирает их по смыслу.",
    };
  }

  // ---------------------------------------------------------------- router
  const routes = {
    "GET /config": () => ({
      vault_path: "demo-vault", vault_ok: true, vault_name: D.vault, chat_provider: "demo", chat_model: "",
      embed_model: "bge-m3", ollama_url: "http://127.0.0.1:11434", temperature: 0.4, context_chunks: 6,
      context_length: 8192, cloud_preset: "openrouter", cloud: {}, presets: D.presets,
    }),
    "GET /vaults/detected": () => [],
    "GET /stats": () => D.stats,
    "GET /graph": () => D.graph,
    "GET /folders": () => D.folders,
    "GET /templates": () => D.templates,
    "GET /notes": (q) => {
      const text = q.get("q") || "", folder = q.get("folder") || "";
      if (!text.trim()) return D.list.filter((n) => !folder || n.folder === folder || n.folder.startsWith(folder + "/"));
      const byPath = Object.fromEntries(D.list.map((n) => [n.path, n]));
      return search(text, 200, folder).map((h) => ({ ...byPath[h.path], snippet: h.snippet, score: h.score }));
    },
    "GET /note": (q) => D.notes[q.get("path")] || fail("Заметка не найдена", 404),
    "GET /resolve": (q) => { const p = resolve(q.get("target") || ""); return p ? { path: p } : fail(`Заметки «${q.get("target")}» пока нет`, 404); },
    "GET /search": (q) => ({ method: "keyword", results: search(q.get("q") || "", +(q.get("k") || 12)) }),
    "GET /ollama/status": () => ({ ok: true, version: "демо", models: [], url: "" }),
    "GET /llm/status": () => ({ provider: "demo", label: "Демо", ok: true, model: "ответы без ИИ", models: [], error: "" }),
    "GET /index/status": () => ({ state: "done", ready: true, notes: D.list.length, chunks: D.list.length, model: "демо",
      updated: Date.parse(D.built) / 1000, stale: 0, embed_model: "демо" }),
    "GET /ai/similar": (q) => {
      const n = NOTES[q.get("path")];
      return { semantic: false, items: search(`${n.title} ${n.tags.join(" ")}`, 10).filter((h) => h.path !== q.get("path")) };
    },
    "POST /templates/render": (q, b) => {
      const tpl = templateFor(b.template, b.folder || "");
      const folder = b.folder || tpl?.folder || "";
      const path = `${folder ? folder + "/" : ""}${b.title || "Без названия"}.md`;
      return { path, content: tpl ? renderTemplate(tpl.name, b.title || "Без названия") : "", warnings: [] };
    },
    "POST /ask": (q, b) => { const a = recallAnswer(b.question); return ndjson([sourcesEvent(a.hits), { type: "model", model: "демо" }, { type: "token", content: a.text }]); },
    "POST /chat": (q, b) => {
      const last = [...b.messages].reverse().find((m) => m.role === "user")?.content || "";
      const a = chatAnswer(last);
      return ndjson([...(b.use_rag === false ? [] : [sourcesEvent(a.hits)]), { type: "model", model: "демо" }, { type: "token", content: a.text }]);
    },
    "POST /ai/summarize": (q, b) => ndjson([{ type: "model", model: "демо" }, { type: "token", content: summarize(b) }]),
    "POST /ai/generate": (q, b) => {
      const tpl = templateFor(b.template, b.folder || "");
      const title = b.title || b.prompt.slice(0, 40);
      const text = tpl ? fillTemplate(renderTemplate(tpl.name, title), `Черновик: ${b.prompt}`)
        : `# ${title}\n\n${b.prompt}\n\n- \n`;
      return ndjson([{ type: "model", model: "демо" }, { type: "token", content: text }, { type: "final", content: text }]);
    },
    "POST /ai/suggest": (q, b) => suggest(b.path),
    "POST /agent/plan": (q, b) => plan(b.instruction),
    "POST /agent/apply": (q, b) => ({ results: b.actions.map((a) => ({ summary: a.summary, ok: false, message: RO })) }),
  };

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const at = url.pathname.indexOf("/api/");
    if (at < 0) return realFetch(input, init);
    const method = (init.method || "GET").toUpperCase();
    const key = `${method} ${url.pathname.slice(at + 4)}`;
    const handler = routes[key];
    if (!handler) return method === "GET" ? fail("В демо недоступно", 404) : fail(RO);
    await sleep(60);
    try {
      const res = handler(url.searchParams, init.body ? JSON.parse(init.body) : {});
      return res instanceof Response ? res : json(res);
    } catch (e) {
      return fail(e.message || String(e), 500);
    }
  };
})();
