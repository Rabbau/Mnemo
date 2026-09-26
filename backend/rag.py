"""Semantic index over vault notes: chunking, embeddings via Ollama, cosine search."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import numpy as np

from .ollama_client import Ollama
from .vault import Note, Vault

MAX_CHUNK = 1200
BATCH = 16


def _prefixes(model: str):
    # nomic-embed-text is trained with task prefixes
    if "nomic" in (model or "").lower():
        return "search_document: ", "search_query: "
    return "", ""


def chunk_note(note: Note) -> list:
    header = f"# {note.title}\nПапка: {note.folder or '/'}\n"
    if note.tags:
        header += "Теги: " + ", ".join(note.tags) + "\n"
    blocks = re.split(r"\n(?=#{1,6}\s)", note.text.strip())
    chunks, buf = [], ""
    for block in blocks:
        for para in re.split(r"\n\s*\n", block):
            para = para.strip()
            if not para:
                continue
            while len(para) > MAX_CHUNK:
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.append(para[:MAX_CHUNK])
                para = para[MAX_CHUNK:]
            if len(buf) + len(para) + 2 > MAX_CHUNK and buf:
                chunks.append(buf)
                buf = ""
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return [header + c for c in chunks] or [header]


class RagIndex:
    def __init__(self, vault_root: Path, data_dir: Path):
        key = hashlib.sha1(str(vault_root).lower().encode("utf-8")).hexdigest()[:12]
        self.dir = data_dir / "index" / key
        self.model: str | None = None
        self.notes: dict[str, float] = {}      # path -> mtime at indexing time
        self.chunks: list[dict] = []           # {"path", "text"} aligned with self.vecs rows
        self.vecs = np.zeros((0, 0), dtype=np.float32)
        self.updated = 0.0
        self.status = {"state": "idle", "done": 0, "total": 0, "error": None}
        self._note_vecs = None
        self._load()

    # ------------------------------------------------------------ persistence

    def _load(self):
        try:
            meta = json.loads((self.dir / "meta.json").read_text(encoding="utf-8"))
            vecs = np.load(self.dir / "vectors.npy")
        except (OSError, ValueError):
            return
        if len(meta.get("chunks", [])) != len(vecs):
            return
        self.model, self.notes, self.chunks = meta.get("model"), meta.get("notes", {}), meta["chunks"]
        self.updated, self.vecs = meta.get("updated", 0), vecs.astype(np.float32)

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        meta = {"model": self.model, "notes": self.notes, "chunks": self.chunks, "updated": self.updated}
        (self.dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        np.save(self.dir / "vectors.npy", self.vecs)

    @property
    def ready(self) -> bool:
        return len(self.chunks) > 0 and self.vecs.shape[0] == len(self.chunks)

    def info(self) -> dict:
        return {
            **self.status, "ready": self.ready, "model": self.model,
            "chunks": len(self.chunks), "notes": len(self.notes), "updated": self.updated,
        }

    def stale_count(self, vault: Vault) -> int:
        changed = sum(1 for p, n in vault.notes.items() if self.notes.get(p) != n.mtime)
        return changed + sum(1 for p in self.notes if p not in vault.notes)

    # ------------------------------------------------------------ building

    async def build(self, vault: Vault, client: Ollama, model: str, full: bool = False):
        self.status = {"state": "running", "done": 0, "total": 0, "error": None}
        try:
            vault.scan(force=True)
            reset = full or self.model != model
            old_notes = {} if reset else dict(self.notes)
            old_chunks = [] if reset else list(self.chunks)
            old_vecs = None if reset else self.vecs

            to_update = [p for p, n in vault.notes.items() if old_notes.get(p) != n.mtime]
            drop = set(to_update) | {p for p in old_notes if p not in vault.notes}
            keep = [i for i, c in enumerate(old_chunks) if c["path"] not in drop]
            chunks = [old_chunks[i] for i in keep]
            vec_parts = [old_vecs[keep]] if keep and old_vecs is not None and old_vecs.size else []

            new_chunks = [{"path": p, "text": t} for p in to_update for t in chunk_note(vault.notes[p])]
            self.status["total"] = len(new_chunks)
            doc_prefix, _ = _prefixes(model)
            for i in range(0, len(new_chunks), BATCH):
                batch = new_chunks[i:i + BATCH]
                embs = await client.embed(model, [doc_prefix + c["text"] for c in batch])
                arr = np.asarray(embs, dtype=np.float32)
                arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
                vec_parts.append(arr)
                self.status["done"] += len(batch)

            notes = {p: m for p, m in old_notes.items() if p not in drop}
            notes.update({p: vault.notes[p].mtime for p in to_update})
            self.chunks = chunks + new_chunks
            self.vecs = np.vstack(vec_parts) if vec_parts else np.zeros((0, 0), dtype=np.float32)
            self.notes, self.model, self.updated = notes, model, time.time()
            self._note_vecs = None
            self._save()
            self.status["state"] = "done"
        except Exception as e:  # reported to the UI
            self.status.update(state="error", error=str(e))

    # ------------------------------------------------------------ search

    async def search(self, client: Ollama, query: str, k: int = 6, folder: str = "") -> list:
        if not self.ready:
            return []
        _, q_prefix = _prefixes(self.model)
        q = np.asarray((await client.embed(self.model, [q_prefix + query]))[0], dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-9
        scores = self.vecs @ q
        if folder:
            mask = np.array([c["path"].startswith(folder + "/") for c in self.chunks])
            scores = np.where(mask, scores, -1.0)
        idx = np.argsort(-scores)[:k]
        return [{"path": self.chunks[i]["path"], "text": self.chunks[i]["text"], "score": float(scores[i])}
                for i in idx if scores[i] > -1]

    def _note_matrix(self):
        if self._note_vecs is None:
            groups: dict[str, list] = {}
            for i, c in enumerate(self.chunks):
                groups.setdefault(c["path"], []).append(i)
            paths = list(groups)
            mat = np.stack([self.vecs[groups[p]].mean(axis=0) for p in paths]) if paths else np.zeros((0, 0))
            mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
            self._note_vecs = (paths, mat.astype(np.float32))
        return self._note_vecs

    def similar(self, path: str, k: int = 10) -> list:
        if not self.ready:
            return []
        paths, mat = self._note_matrix()
        if path not in paths:
            return []
        scores = mat @ mat[paths.index(path)]
        order = [i for i in np.argsort(-scores) if paths[i] != path][:k]
        return [{"path": paths[i], "score": float(scores[i])} for i in order]
