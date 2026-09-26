"""FastAPI server: REST API for the vault, statistics and AI features + static frontend."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .ai import AI
from .cloud import PRESETS, CloudLLM
from .manual import LABEL as MANUAL_LABEL, ManualLLM, ManualNeeded, start_session
from .ollama_client import Ollama, OllamaError
from .rag import RagIndex
from .templates import Templates
from .vault import Vault

FRONTEND = config.APP_DIR / "frontend"


class State:
    def __init__(self):
        self.cfg = config.load()
        self.vault: Vault | None = None
        self.index: RagIndex | None = None
        self.templates: Templates | None = None
        self.index_task: asyncio.Task | None = None
        self.ai = AI(self)
        self.open_vault()

    def open_vault(self):
        p = self.cfg.get("vault_path")
        self.vault = self.index = self.templates = None
        if p and Path(p).is_dir():
            self.vault = Vault(p, config.DATA_DIR / "backups")
            self.templates = Templates(self.vault.root)
            if self.templates.folder:
                # templates are not notes: keep them out of stats, graph and search
                self.vault.exclude_folders = {self.templates.folder.lower()}
            self.index = RagIndex(self.vault.root, config.DATA_DIR)

    @property
    def ollama(self) -> Ollama:
        """Local Ollama: always used for embeddings (search index)."""
        return Ollama(self.cfg.get("ollama_url"))

    @property
    def cloud(self) -> CloudLLM:
        preset = self.cfg.get("cloud_preset") or "openrouter"
        prof = config.cloud_profile(self.cfg, preset)
        return CloudLLM(preset, prof["base_url"], prof["api_key"])

    @property
    def llm(self):
        """Chat model provider: local Ollama, a cloud OpenAI-compatible API or the manual web-chat mode."""
        provider = self.cfg.get("chat_provider")
        if provider == "cloud":
            return self.cloud
        if provider == "manual":
            return ManualLLM()
        return self.ollama

    def v(self) -> Vault:
        if not self.vault:
            raise HTTPException(400, "Хранилище не выбрано. Укажи путь в настройках.")
        self.vault.scan()
        return self.vault

    def note(self, path: str) -> str:
        v = self.v()
        found = v.find_note(path)
        if not found:
            raise HTTPException(404, f"Заметка не найдена: {path}")
        return found


S = State()
app = FastAPI(title="Mnemo")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


def ndjson(gen, manual_answers: list | None = None):
    """Wrap an async generator of dicts into an NDJSON stream; errors become {"type":"error"} events.
    In web-chat mode a model call ends the stream with {"type":"manual", prompt...} (see manual.py)."""
    async def body():
        start_session(manual_answers)  # runs inside the response task, where the generator executes
        try:
            async for ev in gen:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
        except ManualNeeded as m:
            yield json.dumps({"type": "manual", **m.info}, ensure_ascii=False) + "\n"
        except (OllamaError, ValueError, KeyError, FileNotFoundError) as e:
            yield json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")


async def ai_call(coro, manual_answers: list | None = None):
    start_session(manual_answers)
    try:
        return await coro
    except ManualNeeded as m:
        return {"manual_required": m.info}
    except (OllamaError, ValueError) as e:
        raise HTTPException(502 if isinstance(e, OllamaError) else 422, str(e))


# ---------------------------------------------------------------- pages & config

@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/config")
def get_config():
    return {**config.public(S.cfg), "vault_ok": S.vault is not None, "vault_name": S.vault.name if S.vault else "",
            "presets": PRESETS}


class ConfigIn(BaseModel):
    vault_path: str | None = None
    ollama_url: str | None = None
    chat_model: str | None = None
    embed_model: str | None = None
    temperature: float | None = None
    context_chunks: int | None = None
    context_length: int | None = None
    chat_provider: str | None = None     # "ollama" | "cloud"
    cloud_preset: str | None = None
    cloud_base_url: str | None = None
    cloud_api_key: str | None = None     # empty = keep the saved key
    cloud_model: str | None = None
    clear_api_key: bool = False


@app.post("/api/config")
def set_config(body: ConfigIn):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    cloud_fields = {k: data.pop(k) for k in ("cloud_base_url", "cloud_api_key", "cloud_model") if k in data}
    clear_key = data.pop("clear_api_key", False)
    if data.get("chat_provider") not in (None, "ollama", "cloud", "manual"):
        raise HTTPException(400, "Неизвестный провайдер")
    if data.get("cloud_preset") is not None and data["cloud_preset"] not in PRESETS:
        raise HTTPException(400, "Неизвестный облачный провайдер")
    if cloud_fields or clear_key:
        preset = data.get("cloud_preset") or S.cfg.get("cloud_preset") or "openrouter"
        profiles = dict(S.cfg.get("cloud") or {})
        prof = config.cloud_profile(S.cfg, preset)
        if "cloud_base_url" in cloud_fields:
            prof["base_url"] = cloud_fields["cloud_base_url"].strip()
        if "cloud_model" in cloud_fields:
            prof["model"] = cloud_fields["cloud_model"].strip()
        if cloud_fields.get("cloud_api_key", "").strip():
            prof["api_key"] = cloud_fields["cloud_api_key"].strip()
        if clear_key:
            prof["api_key"] = ""
        profiles[preset] = prof
        data["cloud"] = profiles
    if "vault_path" in data:
        p = Path(data["vault_path"].strip().strip('"'))
        if not p.is_dir():
            raise HTTPException(400, f"Папка не найдена: {p}")
        data["vault_path"] = str(p.resolve())
    S.cfg.update(data)
    config.save(S.cfg)
    if "vault_path" in data:
        S.open_vault()
    return get_config()


@app.get("/api/vaults/detected")
def detected_vaults():
    return config.detect_obsidian_vaults()


# ---------------------------------------------------------------- vault data

@app.get("/api/stats")
def stats(refresh: bool = False):
    v = S.v()
    if refresh:
        v.scan(force=True)
    return v.stats()


@app.get("/api/graph")
def graph():
    return S.v().graph()


@app.get("/api/folders")
def folders():
    return S.v().folders


@app.get("/api/notes")
def notes(q: str = "", folder: str = "", limit: int = 2000):
    v = S.v()
    if q.strip():
        hits = v.keyword_search(q, limit, folder)
        extra = {p["path"]: p for p in v.list_notes(folder)}
        return [{**extra[h["path"]], "snippet": h["snippet"], "score": h["score"]} for h in hits]
    return v.list_notes(folder)[:limit]


@app.get("/api/note")
def note(path: str):
    return S.v().note_details(S.note(path))


@app.get("/api/resolve")
def resolve(target: str, src: str = ""):
    v = S.v()
    p = v.resolve(target, src or None)
    if not p:
        raise HTTPException(404, f"Заметка «{target}» не существует")
    return {"path": p}


@app.get("/api/attachment")
def attachment(target: str, src: str = ""):
    v = S.v()
    p = v.resolve_file(target, src or None)
    if not p:
        raise HTTPException(404, "Файл не найден")
    return FileResponse(v.safe_path(p))


class NoteIn(BaseModel):
    path: str
    content: str = ""


@app.post("/api/note/save")
def note_save(body: NoteIn):
    S.v().save(S.note(body.path), body.content)
    return {"ok": True}


@app.post("/api/note/create")
def note_create(body: NoteIn):
    try:
        return {"path": S.v().create(body.path, body.content)}
    except (FileExistsError, ValueError) as e:
        raise HTTPException(400, str(e))


class MoveIn(BaseModel):
    path: str
    new_path: str


@app.post("/api/note/move")
def note_move(body: MoveIn):
    try:
        return S.v().move(S.note(body.path), body.new_path)
    except (FileExistsError, ValueError) as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- ollama & index

@app.get("/api/ollama/status")
async def ollama_status():
    try:
        version = await S.ollama.version()
        models = await S.ollama.models()
        return {"ok": True, "version": version, "models": models, "url": S.ollama.url}
    except OllamaError as e:
        return {"ok": False, "error": str(e), "models": [], "url": S.ollama.url}


@app.get("/api/llm/status")
async def llm_status():
    """State of the active chat provider (local Ollama or cloud API)."""
    if S.cfg.get("chat_provider") == "manual":
        return {"provider": "manual", "label": MANUAL_LABEL, "ok": True, "model": "копировать и вставлять",
                "models": [], "error": ""}
    if S.cfg.get("chat_provider") != "cloud":
        st = await ollama_status()
        chat = [m for m in st["models"] if not m["embedding"]]
        model = S.cfg.get("chat_model") or (chat[0]["name"] if chat else "")
        return {"provider": "ollama", "label": "Ollama", "ok": st["ok"] and bool(chat), "model": model,
                "models": chat, "error": st.get("error") or ("" if chat else "В Ollama нет чат-моделей")}
    cloud = S.cloud
    prof = config.cloud_profile(S.cfg)
    label = PRESETS[cloud.provider]["label"]
    try:
        await cloud.verify_key()
        models = await cloud.models()
    except OllamaError as e:
        return {"provider": "cloud", "label": label, "ok": False, "model": prof["model"], "models": [], "error": str(e)}
    error = "" if prof["model"] else "Выбери модель"
    return {"provider": "cloud", "label": label, "ok": not error, "model": prof["model"], "models": models, "error": error}


@app.get("/api/index/status")
def index_status():
    if not S.index:
        return {"state": "idle", "ready": False, "chunks": 0, "notes": 0, "stale": 0}
    S.vault.scan()
    return {**S.index.info(), "stale": S.index.stale_count(S.vault), "total_notes": len(S.vault.notes),
            "embed_model": S.cfg.get("embed_model")}


class BuildIn(BaseModel):
    full: bool = False


@app.post("/api/index/build")
async def index_build(body: BuildIn):
    S.v()
    if S.index_task and not S.index_task.done():
        raise HTTPException(409, "Индексация уже идёт")
    model = S.cfg.get("embed_model") or "nomic-embed-text"
    S.index_task = asyncio.create_task(S.index.build(S.vault, S.ollama, model, body.full))
    await asyncio.sleep(0.05)
    return S.index.info()


# ---------------------------------------------------------------- AI

class ChatIn(BaseModel):
    manual_answers: list = []
    messages: list
    use_rag: bool = True
    folder: str = ""


@app.post("/api/chat")
def chat(body: ChatIn):
    S.v()
    return ndjson(S.ai.chat(body.messages, body.use_rag, body.folder), body.manual_answers)


class SummaryIn(BaseModel):
    manual_answers: list = []
    path: str = ""
    folder: str | None = None


@app.post("/api/ai/summarize")
def summarize(body: SummaryIn):
    S.v()
    path = S.note(body.path) if body.path else ""
    return ndjson(S.ai.summarize(path, body.folder if body.folder is not None else ""), body.manual_answers)


class GenerateIn(BaseModel):
    manual_answers: list = []
    prompt: str
    use_context: bool = True
    template: str = ""      # "" = none, "auto" = folder's template, or a template name
    title: str = ""
    folder: str = ""


@app.post("/api/ai/generate")
def generate(body: GenerateIn):
    S.v()
    return ndjson(S.ai.generate(body.prompt, body.use_context, body.template, body.title.strip(), body.folder.strip("/")),
                  body.manual_answers)


# ---------------------------------------------------------------- quick find / ask

@app.get("/api/search")
async def search(q: str, k: int = 12, folder: str = ""):
    S.v()
    if not q.strip():
        return {"method": "", "results": []}
    return await S.ai.search(q, k, folder)


class AskIn(BaseModel):
    manual_answers: list = []
    question: str
    folder: str = ""


@app.post("/api/ask")
def ask(body: AskIn):
    S.v()
    return ndjson(S.ai.ask(body.question, body.folder), body.manual_answers)


# ---------------------------------------------------------------- templates

@app.get("/api/templates")
def templates():
    S.v()
    t = S.templates
    return {"folder": t.folder if t else "", "folder_map": t.folder_map if t else {}, "items": t.list() if t else []}


class FromTemplateIn(BaseModel):
    title: str
    folder: str = ""
    template: str = ""      # "" = empty note, "auto" = folder's template


def _render_for(body: FromTemplateIn) -> tuple[str, str, list]:
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Укажи название заметки")
    tpl = S.ai.pick_template(body.template, body.folder.strip("/")) if body.template else None
    folder = body.folder.strip("/") or (tpl["folder"] if tpl else "")
    if not tpl:
        return f"{folder}/{title}" if folder else title, "", []
    content, warnings = S.templates.render(tpl["name"], title, folder)
    return f"{folder}/{title}" if folder else title, content, warnings


@app.post("/api/templates/render")
def template_render(body: FromTemplateIn):
    S.v()
    try:
        path, content, warnings = _render_for(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"path": S.vault.norm_note_path(path), "content": content, "warnings": warnings}


@app.post("/api/note/from-template")
def note_from_template(body: FromTemplateIn):
    v = S.v()
    try:
        path, content, _ = _render_for(body)
        return {"path": v.create(path, content)}
    except (FileExistsError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/ai/suggest")
async def suggest(body: SummaryIn):
    path = S.note(body.path)
    return await ai_call(S.ai.suggest(path), body.manual_answers)


@app.get("/api/ai/similar")
async def similar(path: str):
    path = S.note(path)
    return {"semantic": bool(S.index and S.index.ready), "items": await S.ai.candidates(path, 12)}


class ApplySuggestIn(BaseModel):
    path: str
    tags: list = []
    links: list = []
    folder: str = ""


@app.post("/api/ai/apply-suggestions")
def apply_suggestions(body: ApplySuggestIn):
    v = S.v()
    path = S.note(body.path)
    result = {"path": path, "tags": [], "links": [], "moved": None}
    try:
        if body.tags:
            result["tags"] = v.add_tags(path, body.tags)
        if body.links:
            result["links"] = v.add_links(path, body.links)
        if body.folder.strip("/"):
            name = path.rsplit("/", 1)[-1]
            moved = v.move(path, f"{body.folder.strip('/')}/{name}")
            result["path"], result["moved"] = moved["path"], moved
    except (FileExistsError, ValueError) as e:
        raise HTTPException(400, str(e))
    return result


class PlanIn(BaseModel):
    manual_answers: list = []
    instruction: str


@app.post("/api/agent/plan")
async def agent_plan(body: PlanIn):
    S.v()
    return await ai_call(S.ai.plan(body.instruction), body.manual_answers)


class ApplyIn(BaseModel):
    actions: list


@app.post("/api/agent/apply")
def agent_apply(body: ApplyIn):
    S.v()
    return {"results": S.ai.apply(body.actions)}
