"""AI features on top of Ollama: RAG chat, summaries, suggestions, note generation and the file agent."""
from __future__ import annotations

import datetime as dt
import json
import re

from . import config
from .ollama_client import OllamaError
from .templates import merge_filled
from .vault import Vault, normalize_tag

CONTEXT_CHARS = 1500


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    for candidate in (text, (re.search(r"\{.*\}", text, re.S) or [None])[0]):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    raise ValueError("Модель вернула ответ не в формате JSON. Попробуй ещё раз или выбери модель побольше.")


def _strip_chunk_header(text: str) -> str:
    lines = text.split("\n")
    while lines and (lines[0].startswith("# ") or lines[0].startswith(("Папка:", "Теги:")) or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines)


class AI:
    def __init__(self, state):
        self.s = state

    @property
    def vault(self) -> Vault:
        return self.s.vault

    def _options(self, **extra) -> dict:
        return {
            "temperature": float(self.s.cfg.get("temperature", 0.4)),
            "num_ctx": int(self.s.cfg.get("context_length", 8192)),
            **extra,
        }

    async def chat_model(self) -> str:
        if self.s.cfg.get("chat_provider") == "manual":
            return "веб-чат"
        if self.s.cfg.get("chat_provider") == "cloud":
            m = config.cloud_profile(self.s.cfg)["model"]
            if not m:
                raise OllamaError("Не выбрана облачная модель. Укажи её в Настройках.")
            return m
        m = self.s.cfg.get("chat_model")
        if m:
            return m
        models = [x for x in await self.s.ollama.models() if not x["embedding"]]
        if not models:
            raise OllamaError("В Ollama нет ни одной чат-модели. Скачай, например: ollama pull qwen2.5:7b")
        return models[0]["name"]

    # ------------------------------------------------------------ retrieval

    def _keyword_frags(self, query: str, k: int, folder: str = "") -> list:
        frags = []
        for h in self.vault.keyword_search(query, k, folder):
            note = self.vault.notes[h["path"]]
            text = note.text
            if len(text) > CONTEXT_CHARS:
                pos = max(0, note.lower.find(h["snippet"][:40].lower()))
                text = text[max(0, pos - 300): pos + CONTEXT_CHARS - 300]
            frags.append({**h, "text": f"# {note.title}\n{text}"})
        return frags

    async def _ranked(self, query: str, k: int, folder: str = "") -> tuple[list, dict, dict, str]:
        """Hybrid ranking of notes: semantic chunks + keyword hits merged with reciprocal rank fusion."""
        sem = []
        idx = self.s.index
        if idx and idx.ready:
            try:
                sem = [h for h in await idx.search(self.s.ollama, query, k * 3, folder) if h["path"] in self.vault.notes]
            except OllamaError:
                sem = []
        kw = self._keyword_frags(query, k * 2, folder)
        chunks: dict[str, list] = {}
        score: dict[str, float] = {}
        for rank, p in enumerate(dict.fromkeys(h["path"] for h in sem)):
            score[p] = score.get(p, 0) + 1 / (60 + rank)
        for h in sem:
            chunks.setdefault(h["path"], []).append(h)
        kw_by_path = {f["path"]: f for f in kw}
        for rank, f in enumerate(kw):
            score[f["path"]] = score.get(f["path"], 0) + 1 / (60 + rank)
        order = sorted(score, key=score.get, reverse=True)
        method = "hybrid" if sem and kw else "semantic" if sem else "keyword"
        return order, chunks, kw_by_path, method

    async def retrieve(self, query: str, k: int | None = None, folder: str = "") -> tuple[list, str]:
        """Returns (fragments, method): up to 2 best fragments per note, best notes first."""
        k = k or int(self.s.cfg.get("context_chunks", 6))
        order, chunks, kw, method = await self._ranked(query, k, folder)
        frags = []
        for p in order:
            brief = self.vault.notes[p].brief()
            parts = chunks.get(p, [])[:2] or ([kw[p]] if p in kw else [])
            for c in parts:
                frags.append({**brief, "text": c["text"], "score": c.get("score", 0)})
            if len(frags) >= k:
                break
        return frags[:k], method

    async def search(self, query: str, k: int = 12, folder: str = "") -> dict:
        """Note-level hybrid search for the quick-find palette."""
        order, chunks, kw, method = await self._ranked(query, k, folder)
        results = []
        for p in order[:k]:
            n = self.vault.notes[p]
            if p in chunks:
                snippet = _strip_chunk_header(chunks[p][0]["text"])[:280]
            else:
                snippet = kw[p]["snippet"]
            snippet = " ".join(re.sub(r"^\s*(#{1,6}|>|[-*]\s\[.\]|[-*])\s*", "", ln) for ln in snippet.splitlines() if ln.strip())
            results.append({**n.brief(), "snippet": snippet.strip(), "tags": n.tags, "mtime": n.mtime})
        return {"method": method, "results": results}

    def format_context(self, frags: list) -> str:
        def head(f):
            n = self.vault.notes.get(f["path"])
            dates = ""
            if n:
                dates = (f", создана {dt.date.fromtimestamp(n.created).isoformat()}, "
                         f"изменена {dt.date.fromtimestamp(n.mtime).isoformat()}")
            return f"### Заметка [[{f['title']}]] ({f['path']}{dates})"
        return "\n\n".join(f"{head(f)}\n{f['text'][:CONTEXT_CHARS]}" for f in frags)

    # ------------------------------------------------------------ chat

    async def chat(self, messages: list, use_rag: bool = True, folder: str = ""):
        model = await self.chat_model()
        system = (
            "Ты — помощник по личной базе знаний пользователя в Obsidian. Отвечай на языке пользователя, "
            "по делу и структурированно (markdown). "
        )
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        if use_rag and last_user:
            frags, method = await self.retrieve(last_user, folder=folder)
            yield {"type": "sources", "method": method, "sources": [
                {"path": f["path"], "title": f["title"], "score": round(f.get("score", 0), 3)} for f in frags]}
            if frags:
                system += (
                    "Ниже приведены фрагменты заметок пользователя. Опирайся на них в первую очередь и "
                    "ссылайся на использованные заметки в формате [[Название заметки]]. Если в заметках нет "
                    "ответа — прямо скажи об этом, а затем можешь ответить из общих знаний, пометив это.\n\n"
                    + self.format_context(frags)
                )
            else:
                system += "Поиск по заметкам ничего не нашёл — скажи об этом и ответь из общих знаний."
        msgs = [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")
        ]
        yield {"type": "model", "model": model}
        async for chunk in self.s.llm.chat_stream(model, msgs, self._options()):
            yield {"type": "token", "content": chunk}

    # ------------------------------------------------------------ summaries & generation

    async def summarize(self, path: str = "", folder: str | None = None):
        model = await self.chat_model()
        if path:
            note = self.vault.notes[path]
            material = f"# {note.title}\n{note.text[:14000]}"
            task = ("Сделай краткое содержание заметки: 1) суть в 1–2 предложениях, 2) ключевые идеи списком, "
                    "3) открытые вопросы или что можно доработать (если есть).")
        else:
            notes = [n for n in self.vault.notes.values()
                     if folder == "" or n.folder == folder or n.folder.startswith(folder + "/")]
            notes.sort(key=lambda n: n.mtime, reverse=True)
            budget, parts = 14000, []
            per = max(300, budget // max(1, len(notes)))
            for n in notes:
                piece = f"## [[{n.title}]]\n{n.text[:per].strip()}"
                if budget - len(piece) < 0:
                    parts.append(f"… и ещё {len(notes) - len(parts)} заметок")
                    break
                parts.append(piece)
                budget -= len(piece)
            material = "\n\n".join(parts)
            task = (f"Это заметки из папки «{folder or 'корень хранилища'}» ({len(notes)} шт.). Сделай обзор: "
                    "основные темы, как заметки связаны между собой, что выглядит незаконченным, "
                    "и предложи 3–5 идей, как улучшить структуру. Упоминая заметки, используй [[Название]].")
        yield {"type": "model", "model": model}
        msgs = [{"role": "system", "content": "Ты помогаешь вести базу знаний Obsidian. Отвечай на русском, в markdown."},
                {"role": "user", "content": f"{task}\n\n---\n{material}"}]
        async for chunk in self.s.llm.chat_stream(model, msgs, self._options()):
            yield {"type": "token", "content": chunk}

    # ------------------------------------------------------------ templates

    FILL_RULES = (
        "Ты заполняешь шаблон заметки Obsidian по запросу пользователя. Правила:\n"
        "- Сохрани frontmatter (блок между ---) без изменений и все заголовки шаблона в том же порядке.\n"
        "- Замени строки-подсказки (цитаты «> ...» с инструкциями) и пустые пункты реальным содержанием.\n"
        "- Если для раздела нет информации, оставь в нём пустой пункт «- ».\n"
        "- Пустые ссылки [[]] замени ссылками на реальные заметки из контекста или удали.\n"
        "- Выводи ТОЛЬКО готовую заметку в markdown: без пояснений и без обрамляющих ```.\n"
        "- Пиши на языке пользователя."
    )

    def pick_template(self, template: str, folder: str) -> dict | None:
        """template: '' = without template, 'auto' = the folder's template, otherwise a template name."""
        tpls = self.s.templates
        if not tpls or not template:
            return None
        if template == "auto":
            return tpls.for_folder(folder) if folder else None
        t = tpls.get(template)
        if not t:
            raise ValueError(f"Шаблон не найден: {template}")
        return t

    def _fill_messages(self, tpl: dict, title: str, folder: str, request: str, frags: list):
        skeleton, _ = self.s.templates.render(tpl["name"], title, folder)
        system = self.FILL_RULES
        if frags:
            system += ("\n\nСвязанные заметки пользователя — используй факты из них и ставь ссылки [[Название]]:\n\n"
                       + self.format_context(frags))
        hint = f"\nКогда используется шаблон: {tpl['hint']}" if tpl.get("hint") else ""
        user = (f"Шаблон «{tpl['name']}»{hint}\n\n--- ШАБЛОН ---\n{skeleton}\n--- КОНЕЦ ШАБЛОНА ---\n\n"
                f"Название заметки: {title}\nЧто нужно написать: {request}")
        return skeleton, [{"role": "system", "content": system}, {"role": "user", "content": user}]

    async def fill_template(self, tpl: dict, title: str, folder: str, request: str, use_context: bool = True) -> str:
        frags = (await self.retrieve(f"{title}. {request}"))[0] if use_context else []
        skeleton, msgs = self._fill_messages(tpl, title, folder, request, frags)
        model = await self.chat_model()
        out = await self.s.llm.chat(model, msgs, self._options())
        return merge_filled(skeleton, out)

    async def generate(self, prompt: str, use_context: bool = True, template: str = "", title: str = "", folder: str = ""):
        model = await self.chat_model()
        tpl = self.pick_template(template, folder)
        if tpl and not title:
            raise ValueError("Для заметки по шаблону укажи название")
        frags = (await self.retrieve(f"{title}. {prompt}" if title else prompt))[0] if use_context else []
        if frags:
            yield {"type": "sources", "sources": [{"path": f["path"], "title": f["title"]} for f in frags]}
        if tpl:
            yield {"type": "template", "name": tpl["name"], "folder": tpl["folder"]}
            skeleton, msgs = self._fill_messages(tpl, title, folder or tpl["folder"], prompt, frags)
        else:
            skeleton = ""
            system = ("Ты пишешь заметки для Obsidian. Выводи ТОЛЬКО содержимое заметки в markdown, без пояснений "
                      "и без обрамляющих ```. Используй заголовки, списки, при необходимости теги #тег. "
                      "Пиши на языке запроса.")
            if frags:
                system += ("\nВот связанные заметки пользователя — используй их и ставь на них ссылки "
                           "[[Название]] там, где это уместно:\n\n" + self.format_context(frags))
            msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": f"Название: {title}\n{prompt}" if title else prompt}]
        yield {"type": "model", "model": model}
        parts = []
        async for chunk in self.s.llm.chat_stream(model, msgs, self._options()):
            parts.append(chunk)
            yield {"type": "token", "content": chunk}
        yield {"type": "final", "content": merge_filled(skeleton, "".join(parts))}

    # ------------------------------------------------------------ quick recall

    RECALL_SYSTEM = (
        "Ты помогаешь пользователю быстро найти или вспомнить что-то в его заметках Obsidian. "
        "Отвечай КОРОТКО: 1–4 предложения или короткий список. Опирайся только на фрагменты заметок ниже. "
        "Обязательно укажи, в какой заметке это есть, в формате [[Название заметки]]. "
        "Если во фрагментах ответа нет — честно скажи, что не нашёл, и подскажи, как переформулировать запрос. "
        "Ничего не выдумывай. Отвечай на языке вопроса."
    )

    async def ask(self, question: str, folder: str = ""):
        model = await self.chat_model()
        k = max(6, int(self.s.cfg.get("context_chunks", 6)))
        frags, method = await self.retrieve(question, k=k, folder=folder)
        yield {"type": "sources", "method": method,
               "sources": [{"path": f["path"], "title": f["title"]} for f in frags]}
        if not frags:
            yield {"type": "token", "content": "В заметках ничего похожего не нашлось. Попробуй другие слова."}
            return
        yield {"type": "model", "model": model}
        msgs = [{"role": "system", "content": self.RECALL_SYSTEM + "\n\n" + self.format_context(frags)},
                {"role": "user", "content": question}]
        async for chunk in self.s.llm.chat_stream(model, msgs, self._options(temperature=0.2)):
            yield {"type": "token", "content": chunk}

    # ------------------------------------------------------------ suggestions

    async def candidates(self, path: str, k: int = 10) -> list:
        note = self.vault.notes[path]
        linked = self.vault.out_edges.get(path, set())
        idx = self.s.index
        if idx and idx.ready and path in idx.notes:
            sims = [s for s in idx.similar(path, k + len(linked)) if s["path"] not in linked]
            return [{**self.vault.notes[s["path"]].brief(), "score": round(s["score"], 3)}
                    for s in sims if s["path"] in self.vault.notes][:k]
        query = note.title + " " + " ".join(note.tags) + " " + note.text[:300]
        hits = self.vault.keyword_search(query, k + len(linked) + 1)
        return [{**h, "score": 0} for h in hits if h["path"] != path and h["path"] not in linked][:k]

    async def suggest(self, path: str) -> dict:
        note = self.vault.notes[path]
        model = await self.chat_model()
        cands = await self.candidates(path)
        tags = [t for t, _ in self.vault.tag_counts().most_common(150)]
        folders = self.vault.folders[:150]
        prompt = (
            "Проанализируй заметку и предложи:\n"
            "1) 3–6 тегов. Предпочитай уже существующие теги хранилища, новые — только если нужно. "
            "Без #, в нижнем регистре, несколько слов через дефис.\n"
            "2) Самую подходящую папку из списка существующих (или новую, если ни одна не подходит).\n"
            "3) Заметки из списка кандидатов, на которые стоит поставить ссылку — только реально связанные по смыслу.\n"
            'Ответ строго в JSON: {"tags": [...], "folder": "...", "links": ["точное название кандидата", ...], '
            '"reason": "1–2 предложения, почему"}\n\n'
            f"Существующие теги: {', '.join(tags) or 'нет'}\n"
            f"Существующие папки: {', '.join(folders) or 'нет'}\n"
            f"Кандидаты для ссылок: {', '.join(c['title'] for c in cands) or 'нет'}\n\n"
            f"Текущая папка заметки: {note.folder or '/'}\nТекущие теги: {', '.join(note.tags) or 'нет'}\n\n"
            f"--- Заметка «{note.title}» ---\n{note.text[:8000]}"
        )
        raw = await self.s.llm.chat(model, [{"role": "user", "content": prompt}], self._options(temperature=0.2), fmt="json")
        data = extract_json(raw)

        new_tags = []
        for t in data.get("tags") or []:
            t = normalize_tag(t)
            if t and t not in note.tags and t not in new_tags:
                new_tags.append(t)
        by_title = {c["title"].lower(): c for c in cands}
        links = []
        for name in data.get("links") or []:
            c = by_title.get(str(name).strip().strip("[]").lower())
            if c and c not in links:
                links.append(c)
        folder = str(data.get("folder") or "").strip().strip("/")
        if folder in ("", "/", ".") or folder == note.folder:
            folder = ""
        return {
            "model": model, "tags": new_tags, "links": links, "folder": folder,
            "folder_exists": folder in self.vault.folders, "current_folder": note.folder,
            "reason": str(data.get("reason") or ""), "candidates": cands,
        }

    # ------------------------------------------------------------ agent

    AGENT_SYSTEM = """Ты — агент, управляющий хранилищем заметок Obsidian. По запросу пользователя составь план изменений.
Верни ТОЛЬКО JSON вида: {"explanation": "кратко, что будет сделано и почему", "actions": [ ... ]}
Доступные действия:
- {"type": "create", "path": "Папка/Название.md", "template": "Название шаблона", "content": "..."} — создать новую заметку.
  Если подходит шаблон — укажи его в "template", а в "content" 1–2 предложения о том, что должно быть в заметке.
  НЕ пиши сам текст заметки: шаблон заполнится отдельно.
  Если шаблона нет — "template": "", а в "content" полный markdown-текст заметки.
- {"type": "append", "path": "Папка/Заметка.md", "content": "текст"} — дописать текст в конец существующей заметки
- {"type": "move", "path": "Папка/Заметка.md", "new_path": "Другая папка/Новое имя.md"} — переместить или переименовать (ссылки на заметку обновятся автоматически)
- {"type": "add_tags", "path": "Папка/Заметка.md", "tags": ["тег1", "тег2"]} — добавить теги
- {"type": "add_links", "path": "Папка/Заметка.md", "links": ["Название другой заметки"]} — добавить ссылки [[...]] на другие заметки
- {"type": "create_folder", "path": "Папка/Подпапка"} — создать папку
- {"type": "trash", "path": "Папка/Заметка.md"} — переместить в корзину (только если пользователь явно просит удалить)
Правила:
- Новые заметки создавай по подходящему шаблону и клади в папку этого шаблона (см. список шаблонов).
- Пути указывай относительно корня хранилища через "/", существующие заметки — ТОЧНО как в списке.
- Не выдумывай несуществующие заметки для move/append/add_tags/add_links/trash.
- Делай только то, о чём просили. Если запрос неясен или невыполним — верни пустой список actions и объясни в explanation.
- Пиши на языке пользователя."""

    MAX_TEMPLATE_FILLS = 6

    def _templates_context(self) -> str:
        tpls = self.s.templates.list() if self.s.templates else []
        if not tpls:
            return "Шаблонов в хранилище нет."
        lines = []
        for t in tpls:
            hint = " ".join(t["hint"].split())[:220]
            lines.append(f"- {t['name']} → папка «{t['folder'] or 'любая'}»: {hint}")
        return "Шаблоны заметок:\n" + "\n".join(lines)

    async def plan(self, instruction: str) -> dict:
        model = await self.chat_model()
        v = self.vault
        v.scan()
        frags, _ = await self.retrieve(instruction, k=6)
        paths = sorted(v.notes, key=lambda p: v.notes[p].mtime, reverse=True)
        mentioned = [p for p in paths if v.notes[p].folder and v.notes[p].folder.lower() in instruction.lower()]
        listed = list(dict.fromkeys(mentioned[:150] + paths[:200]))
        ctx = (
            f"Папки хранилища:\n{chr(10).join(v.folders[:200]) or '(нет папок)'}\n\n"
            f"{self._templates_context()}\n\n"
            f"Ссылки на заметки, которых ещё нет (кандидаты на создание): "
            f"{', '.join(dict.fromkeys(t for _, t in v.broken)) or 'нет'}\n\n"
            f"Заметки (путь | теги) — {len(listed)} из {len(paths)}:\n"
            + "\n".join(f"{p} | {', '.join(v.notes[p].tags)}" for p in listed)
            + "\n\nРелевантные запросу заметки (фрагменты):\n"
            + "\n\n".join(f"### {f['path']}\n{f['text'][:400]}" for f in frags)
        )
        msgs = [{"role": "system", "content": self.AGENT_SYSTEM},
                {"role": "user", "content": f"{ctx}\n\n=== Запрос пользователя ===\n{instruction}"}]
        raw = await self.s.llm.chat(model, msgs, self._options(temperature=0.2), fmt="json")
        data = extract_json(raw)
        actions = self.validate(data.get("actions") if isinstance(data.get("actions"), list) else [])

        # second pass: fill the chosen templates so the user reviews real content before applying
        fills = 0
        for a in actions:
            if not (a["ok"] and a["type"] == "create" and a.get("template")):
                continue
            tpl = self.s.templates.get(a["template"])
            title = a["path"].rsplit("/", 1)[-1][:-3]
            folder = a["path"].rsplit("/", 1)[0] if "/" in a["path"] else ""
            brief = a.get("content") or instruction
            a["brief"] = brief
            if fills < self.MAX_TEMPLATE_FILLS:
                fills += 1
                try:
                    a["content"] = await self.fill_template(tpl, title, folder, f"{brief}\n(Общая задача: {instruction})")
                    continue
                except (OllamaError, ValueError):
                    pass
            a["content"] = self.s.templates.render(tpl["name"], title, folder)[0]
        return {"model": model, "explanation": str(data.get("explanation") or ""), "actions": actions}

    def _similar_titles(self, title: str, limit: int = 3) -> list:
        t = title.lower().strip()
        if len(t) < 4:
            return []
        out = []
        for n in self.vault.notes.values():
            nt = n.title.lower()
            # the new title inside an existing one, or an existing title covering most of the new one
            if nt != t and (t in nt or (len(nt) >= 4 and nt in t and len(nt) >= 0.6 * len(t))):
                out.append(n.title)
        return sorted(out, key=len)[:limit]

    def validate(self, actions: list) -> list:
        v = self.vault
        v.scan(force=True)
        out = []
        for a in actions:
            if not isinstance(a, dict):
                continue
            a = dict(a)
            t = str(a.get("type", "")).lower()
            a["type"], a["ok"], a["error"] = t, True, None
            try:
                if t == "create":
                    tpl = self.s.templates.get(str(a.get("template") or "")) if self.s.templates else None
                    a["template"] = tpl["name"] if tpl else ""
                    path = str(a.get("path") or "").replace("\\", "/").strip("/")
                    if tpl and tpl["folder"] and "/" not in path:
                        path = f"{tpl['folder']}/{path}"  # a bare name goes to the template's folder
                    if not tpl and self.s.templates and "/" in path and not a.get("no_template"):
                        # like Templater's folder templates: a note created in a folder gets that folder's template
                        tpl = self.s.templates.for_folder(path.rsplit("/", 1)[0])
                        a["template"] = tpl["name"] if tpl else ""
                    a["path"] = v.norm_note_path(path)
                    v.safe_path(a["path"])
                    a["content"] = str(a.get("content") or "")
                    if a["path"] in v.notes:
                        raise ValueError("такая заметка уже существует")
                    similar = self._similar_titles(a["path"].rsplit("/", 1)[-1][:-3])
                    if similar:
                        a["warning"] = "Похожие заметки уже есть: " + ", ".join(f"[[{t}]]" for t in similar)
                    a["summary"] = f"Создать заметку {a['path']}" + (f" по шаблону «{tpl['name']}»" if tpl else "")
                elif t == "create_folder":
                    a["path"] = str(a.get("path") or "").strip("/")
                    v.safe_path(a["path"])
                    a["summary"] = f"Создать папку {a['path']}"
                elif t in ("append", "move", "add_tags", "add_links", "trash"):
                    found = v.find_note(str(a.get("path") or ""))
                    if not found:
                        raise ValueError(f"заметка не найдена: {a.get('path')}")
                    a["path"] = found
                    if t == "append":
                        a["content"] = str(a.get("content") or "")
                        a["summary"] = f"Дописать в {found}"
                    elif t == "move":
                        a["new_path"] = v.norm_note_path(a.get("new_path", ""))
                        v.safe_path(a["new_path"])
                        if a["new_path"] == found:
                            raise ValueError("новый путь совпадает со старым")
                        if a["new_path"] in v.notes:
                            raise ValueError(f"уже существует: {a['new_path']}")
                        a["backlinks"] = len(v.in_edges.get(found, ()))
                        a["summary"] = f"Переместить {found} → {a['new_path']}"
                    elif t == "add_tags":
                        a["tags"] = [normalize_tag(x) for x in (a.get("tags") or []) if normalize_tag(x)]
                        if not a["tags"]:
                            raise ValueError("нет тегов")
                        a["summary"] = f"Добавить теги в {found}: " + ", ".join("#" + x for x in a["tags"])
                    elif t == "add_links":
                        links = [str(x).strip().strip("[]") for x in (a.get("links") or [])]
                        resolved = [v.find_note(x) for x in links]
                        a["links"] = [r for r in resolved if r and r != found]
                        if not a["links"]:
                            raise ValueError("ни одна из заметок для ссылок не найдена: " + ", ".join(links))
                        a["summary"] = f"Добавить ссылки в {found}: " + ", ".join(
                            f"[[{v.notes[x].title}]]" for x in a["links"])
                    else:
                        a["summary"] = f"Переместить в корзину {found}"
                else:
                    raise ValueError(f"неизвестное действие «{t}»")
            except (ValueError, OSError) as e:
                a["ok"], a["error"] = False, str(e)
                a.setdefault("summary", f"{t}: {a.get('path', '')}")
            out.append(a)
        return out

    def apply(self, actions: list) -> list:
        v = self.vault
        results = []
        for raw in actions:
            # validate right before applying: earlier actions may create folders/notes later ones rely on
            checked = self.validate([raw])
            if not checked:
                continue
            a = checked[0]
            if not a["ok"]:
                results.append({"summary": a["summary"], "ok": False, "message": a["error"]})
                continue
            try:
                t = a["type"]
                if t == "create":
                    content = a["content"]
                    if not content.strip() and a.get("template"):
                        folder = a["path"].rsplit("/", 1)[0] if "/" in a["path"] else ""
                        content = self.s.templates.render(a["template"], a["path"].rsplit("/", 1)[-1][:-3], folder)[0]
                    msg = "создана " + v.create(a["path"], content)
                elif t == "create_folder":
                    msg = "создана папка " + v.create_folder(a["path"])
                elif t == "append":
                    v.append(a["path"], a["content"])
                    msg = "текст добавлен"
                elif t == "move":
                    r = v.move(a["path"], a["new_path"])
                    msg = f"перемещено; ссылки обновлены в {len(r['updated_links_in'])} заметках"
                elif t == "add_tags":
                    added = v.add_tags(a["path"], a["tags"])
                    msg = "добавлены теги: " + (", ".join(added) or "нет новых")
                elif t == "add_links":
                    added = v.add_links(a["path"], a["links"])
                    msg = f"добавлено ссылок: {len(added)}"
                else:
                    msg = "в корзине: " + v.trash(a["path"])
                results.append({"summary": a["summary"], "ok": True, "message": msg})
            except Exception as e:
                results.append({"summary": a["summary"], "ok": False, "message": str(e)})
        return results
