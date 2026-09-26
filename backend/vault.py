"""Obsidian vault model: parsing notes, resolving links, statistics and safe file operations."""
from __future__ import annotations

import datetime as dt
import os
import posixpath
import re
import shutil
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import yaml

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
CODE_BLOCK_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]|#^]*)([#^][^\[\]|]*)?(?:\|([^\[\]]*))?\]\]")
MDLINK_RE = re.compile(r"(!?)\[[^\]]*\]\(<?([^)<>\s]+)>?\)")
TAG_RE = re.compile(r"(?:^|(?<=[\s(,;]))#([\w\-/]+)", re.M)
WORD_RE = re.compile(r"[\w’'-]+")
BAD_NAME_CHARS_RE = re.compile(r'[\\:*?"<>|]')
DATE_KEYS = ("created", "date", "created_at", "creation_date", "Created", "Date", "создано", "Создано", "дата", "Дата")
ROOT_LABEL = "(корень)"
YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
# question/filler words that only add noise to keyword search ("где я писал про ...")
STOP_WORDS = set("""
где что как когда какой какая какое какие каких почему зачем кто чем сколько есть ли это эта эти этот
про для при над под без или все всё мой моя мои моё мне меня писал писала записывал записал заметка заметке
заметки заметках заметку найди найти напомни вспомни покажи расскажи объясни был была было были тоже также
очень можно нужно надо там тут вот она они оно его еще ещё уже the and for what where how
""".split())


# ---------------------------------------------------------------- helpers

def split_frontmatter(content: str):
    """Returns (frontmatter dict, body, raw frontmatter block)."""
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}, content, ""
    try:
        fm = yaml.load(m.group(1), Loader=YAML_LOADER) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, content[m.end():], m.group(0)


def normalize_tag(t) -> str:
    t = str(t).strip().lstrip("#").strip().strip("/")
    return re.sub(r"\s+", "-", t).lower()


def _valid_tag(t: str) -> bool:
    return bool(t) and bool(re.search(r"[^\d_\-/]", t))


def _as_list(v, split=True) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [s for s in re.split(r"[,\s]+" if split else r",", v) if s.strip()]
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v if x is not None]
    return [str(v)]


def _parse_date(v):
    if isinstance(v, dt.datetime):
        return v.timestamp()
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day).timestamp()
    if isinstance(v, str):
        s = v.strip().replace("Z", "")
        try:
            return dt.datetime.fromisoformat(s).timestamp()
        except ValueError:
            pass
        for fmt in ("%d.%m.%Y", "%Y/%m/%d", "%d.%m.%Y %H:%M"):
            try:
                return dt.datetime.strptime(s, fmt).timestamp()
            except ValueError:
                pass
    return None


def _day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _stem_token(t: str) -> str:
    # crude stemming so "заметки" matches "заметка" etc.
    return t[: max(3, len(t) - 2)] if len(t) >= 5 else t


@dataclass
class Note:
    path: str          # relative posix path with .md
    name: str          # file stem
    folder: str        # relative folder, "" for root
    title: str
    text: str          # body without frontmatter
    words: int
    size: int
    mtime: float
    created: float
    tags: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    links: list = field(default_factory=list)   # [(target, is_embed)]
    frontmatter: dict = field(default_factory=dict)
    _lower: str = ""

    @property
    def lower(self) -> str:
        if not self._lower:
            self._lower = self.text.lower()
        return self._lower

    def brief(self) -> dict:
        return {"path": self.path, "title": self.title, "folder": self.folder}


# ---------------------------------------------------------------- vault

class Vault:
    def __init__(self, root: str, backup_root: Path):
        self.root = Path(root).resolve()
        self.name = self.root.name
        self.backup_root = backup_root / re.sub(r"[^\w\-]+", "_", self.name)
        self.notes: dict[str, Note] = {}
        self.files: list[str] = []
        self.folders: list[str] = []
        self.out_edges: dict[str, set] = {}
        self.in_edges: dict[str, set] = {}
        self.broken: list[tuple[str, str]] = []
        self.attachment_links = 0
        self.last_scan = 0.0
        self.exclude_folders: set[str] = set()   # e.g. the templates folder: not notes, not analytics
        self._by_path: dict[str, str] = {}
        self._by_name: dict[str, list] = {}
        self._files_by_path: dict[str, str] = {}
        self._files_by_name: dict[str, list] = {}

    # ------------------------------------------------------------ scanning

    def scan(self, force: bool = False) -> None:
        if not force and time.time() - self.last_scan < 3:
            return
        seen, files, folders = set(), [], []
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_dir = os.path.relpath(dirpath, self.root).replace(os.sep, "/")
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and d != "node_modules"
                and (d if rel_dir == "." else f"{rel_dir}/{d}").lower() not in self.exclude_folders
            )
            if rel_dir != ".":
                folders.append(rel_dir)
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
                # Excalidraw drawings are stored as .md but are attachments, not notes
                if not fn.lower().endswith(".md") or fn.lower().endswith(".excalidraw.md"):
                    files.append(rel)
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                seen.add(rel)
                old = self.notes.get(rel)
                if old and old.mtime == st.st_mtime and old.size == st.st_size:
                    continue
                self.notes[rel] = self._parse_note(rel, full, st)
        for rel in list(self.notes):
            if rel not in seen:
                del self.notes[rel]
        self.files, self.folders = files, folders
        self._build_indices()
        self.last_scan = time.time()

    def _parse_note(self, rel: str, full: str, st) -> Note:
        try:
            with open(full, "r", encoding="utf-8", errors="replace", newline="") as f:
                content = f.read()
        except OSError:
            content = ""
        fm, body, raw_fm = split_frontmatter(content)
        clean = INLINE_CODE_RE.sub(" ", CODE_BLOCK_RE.sub(" ", body))

        links = []
        for src in (raw_fm, clean):
            for m in WIKILINK_RE.finditer(src):
                links.append((m.group(2).strip(), bool(m.group(1))))
        for m in MDLINK_RE.finditer(clean):
            target = m.group(2)
            if "://" in target or target.startswith(("mailto:", "obsidian:", "#")):
                continue
            target = unquote(target.split("#", 1)[0])
            if target:
                links.append((target, bool(m.group(1))))

        tags = set()
        for t in _as_list(fm.get("tags")) + _as_list(fm.get("tag")):
            t = normalize_tag(t)
            if _valid_tag(t):
                tags.add(t)
        for m in TAG_RE.finditer(WIKILINK_RE.sub(" ", clean)):
            t = normalize_tag(m.group(1))
            if _valid_tag(t):
                tags.add(t)

        created = None
        for k in DATE_KEYS:
            if k in fm:
                created = _parse_date(fm[k])
                if created:
                    break
        if not created:
            created = getattr(st, "st_birthtime", None) or (st.st_ctime if os.name == "nt" else st.st_mtime)

        stem = rel.rsplit("/", 1)[-1][:-3]
        title = fm.get("title") if isinstance(fm.get("title"), str) else stem
        return Note(
            path=rel, name=stem, folder=rel.rsplit("/", 1)[0] if "/" in rel else "",
            title=title, text=body, words=len(WORD_RE.findall(body)), size=st.st_size,
            mtime=st.st_mtime, created=created, tags=sorted(tags),
            aliases=_as_list(fm.get("aliases") or fm.get("alias"), split=False),
            links=links, frontmatter=fm,
        )

    def _build_indices(self) -> None:
        by_path, by_name = {}, defaultdict(list)
        for rel in self.notes:
            by_path[rel[:-3].lower()] = rel
            by_name[rel.rsplit("/", 1)[-1][:-3].lower()].append(rel)
        fpath, fname = {}, defaultdict(list)
        for rel in self.files:
            fpath[rel.lower()] = rel
            fname[rel.rsplit("/", 1)[-1].lower()].append(rel)
        self._by_path, self._by_name = by_path, by_name
        self._files_by_path, self._files_by_name = fpath, fname

        out_e, in_e, broken, att = defaultdict(set), defaultdict(set), [], 0
        for src, note in self.notes.items():
            for target, _embed in note.links:
                dst = self.resolve(target, src)
                if dst:
                    if dst != src:
                        out_e[src].add(dst)
                        in_e[dst].add(src)
                elif self.resolve_file(target, src):
                    att += 1
                else:
                    broken.append((src, target))
        self.out_edges, self.in_edges, self.broken, self.attachment_links = out_e, in_e, broken, att

    # ------------------------------------------------------------ link resolution

    def _norm_target(self, target: str, src: str | None) -> str:
        t = target.strip().replace("\\", "/").lstrip("/")
        if src and t.startswith(("./", "../")):
            base = posixpath.dirname(src)
            t = posixpath.normpath(posixpath.join(base, t))
        return t

    def resolve(self, target: str, src: str | None = None) -> str | None:
        """Resolve a link target to a note path the way Obsidian does (shortest-path matching)."""
        t = self._norm_target(target, src)
        if not t:
            return src
        tl = t.lower()
        if tl.endswith(".md"):
            tl = tl[:-3]
        elif "." in tl.rsplit("/", 1)[-1] and tl.rsplit(".", 1)[-1] in ATTACHMENT_EXT:
            return None
        if tl in self._by_path:
            return self._by_path[tl]
        cands = self._by_name.get(tl.rsplit("/", 1)[-1], [])
        if "/" in tl:
            cands = [c for c in cands if c.lower()[:-3].endswith(tl)]
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        src_folder = src.rsplit("/", 1)[0] if src and "/" in src else ""
        same = [c for c in cands if (c.rsplit("/", 1)[0] if "/" in c else "") == src_folder]
        return same[0] if same else min(cands, key=lambda c: (c.count("/"), len(c)))

    def resolve_file(self, target: str, src: str | None = None) -> str | None:
        tl = self._norm_target(target, src).lower()
        if not tl:
            return None
        for key in (tl, tl + ".md"):
            if key in self._files_by_path:
                return self._files_by_path[key]
        name = tl.rsplit("/", 1)[-1]
        cands = self._files_by_name.get(name, []) or self._files_by_name.get(name + ".md", [])
        if "/" in tl:
            cands = [c for c in cands if c.lower().endswith(tl)]
        return cands[0] if cands else None

    def link_text_for(self, rel: str, exclude: str | None = None) -> str:
        stem = rel.rsplit("/", 1)[-1][:-3]
        dup = [p for p in self._by_name.get(stem.lower(), []) if p not in (rel, exclude)]
        return rel[:-3] if dup else stem

    # ------------------------------------------------------------ queries

    def note_details(self, rel: str) -> dict:
        self.scan()
        note = self.notes.get(rel)
        if not note:
            raise FileNotFoundError(rel)
        return {
            "path": rel, "title": note.title, "folder": note.folder, "content": self.read(rel),
            "tags": note.tags, "aliases": note.aliases, "words": note.words,
            "mtime": note.mtime, "created": note.created,
            "backlinks": [self.notes[p].brief() for p in sorted(self.in_edges.get(rel, ())) if p in self.notes],
            "outlinks": [self.notes[p].brief() for p in sorted(self.out_edges.get(rel, ())) if p in self.notes],
            "broken": sorted({t for s, t in self.broken if s == rel}),
        }

    def list_notes(self, folder: str = "") -> list:
        self.scan()
        out = []
        for p, n in self.notes.items():
            if folder and not (n.folder == folder or n.folder.startswith(folder + "/")):
                continue
            out.append({
                "path": p, "title": n.title, "folder": n.folder, "words": n.words, "tags": n.tags,
                "mtime": n.mtime, "created": n.created,
                "in": len(self.in_edges.get(p, ())), "out": len(self.out_edges.get(p, ())),
            })
        return out

    def keyword_search(self, query: str, limit: int = 20, folder: str = "") -> list:
        self.scan()
        q = query.lower().strip()
        tokens = [_stem_token(t) for t in WORD_RE.findall(q) if len(t) >= 3 and t not in STOP_WORDS] or ([q] if q else [])
        if not tokens:
            return []
        results = []
        for p, n in self.notes.items():
            if folder and not (n.folder == folder or n.folder.startswith(folder + "/")):
                continue
            title_l, text_l = n.title.lower(), n.lower
            score, first = 0.0, -1
            for t in tokens:
                c = text_l.count(t)
                score += title_l.count(t) * 10 + min(c, 20) + sum(5 for tag in n.tags if t in tag)
                if c and (first < 0 or text_l.find(t) < first):
                    first = text_l.find(t)
            if q in title_l:
                score += 30
            if score > 0:
                start = max(0, first - 120) if first >= 0 else 0
                snippet = n.text[start:start + 320].strip()
                results.append({**n.brief(), "score": score, "snippet": snippet})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def tag_counts(self) -> Counter:
        c = Counter()
        for n in self.notes.values():
            c.update(n.tags)
        return c

    # ------------------------------------------------------------ statistics

    def stats(self) -> dict:
        self.scan()
        notes = self.notes
        n_notes = len(notes)
        in_deg = {p: len(self.in_edges.get(p, ())) for p in notes}
        out_deg = {p: len(self.out_edges.get(p, ())) for p in notes}
        edges = sum(out_deg.values())
        total_words = sum(n.words for n in notes.values())

        # folders (recursive totals)
        fstats = defaultdict(lambda: {"notes": 0, "direct": 0, "words": 0, "links": 0})
        for p, note in notes.items():
            parts = note.folder.split("/") if note.folder else []
            chain = [""] + ["/".join(parts[: i + 1]) for i in range(len(parts))]
            for f in chain:
                fs = fstats[f]
                fs["notes"] += 1
                fs["words"] += note.words
                fs["links"] += out_deg[p]
            fstats[note.folder]["direct"] += 1
        for f in self.folders:
            fstats[f]  # include empty folders
        folders = [
            {"folder": f or ROOT_LABEL, "path": f, "depth": f.count("/") + 1 if f else 0, **v}
            for f, v in sorted(fstats.items())
        ]
        top_folders = [x for x in folders if x["depth"] == 1]
        if fstats[""]["direct"]:
            top_folders.append({"folder": ROOT_LABEL, "path": "", "depth": 0,
                                "notes": fstats[""]["direct"], "words": 0, "links": 0, "direct": 0})
        top_folders.sort(key=lambda x: x["notes"], reverse=True)

        tags = self.tag_counts()
        orphans = [p for p in notes if in_deg[p] == 0 and out_deg[p] == 0]
        unresolved = Counter(t for _, t in self.broken)

        def top(metric, k=15):
            items = sorted(notes, key=lambda p: metric(p), reverse=True)[:k]
            return [{**notes[p].brief(), "value": metric(p)} for p in items if metric(p) > 0]

        # activity / growth
        modified, created, months = Counter(), Counter(), Counter()
        for n in notes.values():
            modified[_day(n.mtime)] += 1
            created[_day(n.created)] += 1
            months[dt.datetime.fromtimestamp(n.created).strftime("%Y-%m")] += 1
        growth, total = [], 0
        for m in sorted(months):
            total += months[m]
            growth.append({"month": m, "added": months[m], "total": total})

        comps = self._components()
        return {
            "vault": self.name,
            "totals": {
                "notes": n_notes,
                "words": total_words,
                "avg_words": round(total_words / n_notes) if n_notes else 0,
                "links": edges,
                "avg_links": round(edges / n_notes, 2) if n_notes else 0,
                "tags": len(tags),
                "notes_with_tags": sum(1 for n in notes.values() if n.tags),
                "folders": len(self.folders),
                "attachments": len(self.files),
                "attachment_links": self.attachment_links,
                "orphans": len(orphans),
                "no_backlinks": sum(1 for p in notes if in_deg[p] == 0),
                "dead_ends": sum(1 for p in notes if out_deg[p] == 0),
                "broken": len(self.broken),
                "empty": sum(1 for n in notes.values() if n.words < 5),
                "components": len(comps),
                "largest_component": max(comps) if comps else 0,
            },
            "top_folders": top_folders,
            "folders": folders,
            "tags": [{"tag": t, "count": c} for t, c in tags.most_common(100)],
            "top_linked": top(lambda p: in_deg[p]),
            "hubs": top(lambda p: in_deg[p] + out_deg[p]),
            "largest": top(lambda p: notes[p].words, 10),
            "recent": [{**notes[p].brief(), "value": notes[p].mtime}
                       for p in sorted(notes, key=lambda p: notes[p].mtime, reverse=True)[:10]],
            "orphans": [notes[p].brief() for p in sorted(orphans)[:300]],
            "empty_notes": [notes[p].brief() for p in sorted(notes) if notes[p].words < 5][:300],
            "broken": [{"source": s, "target": t} for s, t in self.broken[:300]],
            "unresolved": [{"target": t, "count": c} for t, c in unresolved.most_common(20)],
            "activity": {"modified": dict(modified), "created": dict(created)},
            "growth": growth,
        }

    def _components(self) -> list:
        parent = {p: p for p in self.notes}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for s, dsts in self.out_edges.items():
            for d in dsts:
                if s in parent and d in parent:
                    parent[find(s)] = find(d)
        sizes = Counter(find(p) for p in self.notes)
        return sorted(sizes.values(), reverse=True)

    def graph(self) -> dict:
        self.scan()
        nodes = []
        for p, n in self.notes.items():
            i, o = len(self.in_edges.get(p, ())), len(self.out_edges.get(p, ()))
            nodes.append({
                "id": p, "name": n.title, "folder": n.folder,
                "top": n.folder.split("/")[0] if n.folder else ROOT_LABEL,
                "in": i, "out": o, "deg": i + o, "words": n.words, "tags": n.tags,
            })
        links = [{"source": s, "target": d} for s, ds in self.out_edges.items() for d in ds]
        return {"nodes": nodes, "links": links}

    # ------------------------------------------------------------ file operations

    def safe_path(self, rel: str) -> Path:
        rel = (rel or "").replace("\\", "/").strip().lstrip("/")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"Путь вне хранилища: {rel}")
        if any(part.startswith(".") for part in p.relative_to(self.root).parts[:-1]) and ".trash" not in p.parts:
            raise ValueError(f"Нельзя изменять служебные папки: {rel}")
        return p

    @staticmethod
    def norm_note_path(rel: str) -> str:
        rel = (rel or "").replace("\\", "/").strip().strip("/")
        parts = [BAD_NAME_CHARS_RE.sub("-", part).strip() for part in rel.split("/") if part.strip()]
        if not parts:
            raise ValueError("Пустой путь")
        rel = "/".join(parts)
        if not rel.lower().endswith(".md"):
            rel += ".md"
        return rel

    def find_note(self, rel: str) -> str | None:
        """Accepts an exact path, a path in any case, or a note name."""
        if not rel:
            return None
        rel = rel.replace("\\", "/").strip().strip("/")
        if rel in self.notes:
            return rel
        return self.resolve(rel)

    def read(self, rel: str) -> str:
        with open(self.safe_path(rel), "r", encoding="utf-8", errors="replace", newline="") as f:
            return f.read()

    def _stamp(self) -> str:
        return dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")

    def _backup(self, rel: str, stamp: str) -> None:
        src = self.safe_path(rel)
        if src.exists():
            dst = self.backup_root / stamp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    def _write(self, rel: str, content: str) -> None:
        p = self.safe_path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(content)

    def save(self, rel: str, content: str) -> str:
        if not self.safe_path(rel).exists():
            raise FileNotFoundError(rel)
        self._backup(rel, self._stamp())
        self._write(rel, content)
        self.scan(force=True)
        return rel

    def create(self, rel: str, content: str) -> str:
        rel = self.norm_note_path(rel)
        if self.safe_path(rel).exists():
            raise FileExistsError(f"Заметка уже существует: {rel}")
        self._write(rel, content)
        self.scan(force=True)
        return rel

    def create_folder(self, rel: str) -> str:
        rel = "/".join(BAD_NAME_CHARS_RE.sub("-", x).strip() for x in rel.replace("\\", "/").split("/") if x.strip())
        if not rel:
            raise ValueError("Пустой путь")
        self.safe_path(rel).mkdir(parents=True, exist_ok=True)
        self.scan(force=True)
        return rel

    def append(self, rel: str, text: str) -> str:
        content = self.read(rel)
        sep = "" if not content or content.endswith("\n\n") else ("\n" if content.endswith("\n") else "\n\n")
        self._backup(rel, self._stamp())
        self._write(rel, content + sep + text.rstrip() + "\n")
        self.scan(force=True)
        return rel

    def add_tags(self, rel: str, tags: list) -> list:
        content = self.read(rel)
        fm, body, raw = split_frontmatter(content)
        existing = [normalize_tag(t) for t in _as_list(fm.get("tags"))]
        current = set(existing) | set(self.notes[rel].tags if rel in self.notes else [])
        new = []
        for t in tags:
            t = normalize_tag(t)
            if _valid_tag(t) and t not in current and t not in new:
                new.append(t)
        if not new:
            return []
        fm["tags"] = existing + new
        dumped = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
        nl = "\r\n" if "\r\n" in content else "\n"
        head = f"---{nl}{dumped.replace(chr(10), nl)}{nl}---{nl}"
        if not raw and body and not body.startswith(("\n", "\r\n")):
            head += nl
        self._backup(rel, self._stamp())
        self._write(rel, head + body)
        self.scan(force=True)
        return new

    def add_links(self, rel: str, targets: list, heading: str = "Связанные заметки") -> list:
        """Append [[links]] to notes that are not linked yet. Targets are note paths or names."""
        self.scan(force=True)
        already = self.out_edges.get(rel, set())
        lines = []
        for t in targets:
            dst = self.find_note(t)
            if not dst or dst == rel or dst in already:
                continue
            link = f"- [[{self.link_text_for(dst)}]]"
            if link not in lines:
                lines.append(link)
        if not lines:
            return []
        content = self.read(rel)
        block = "\n".join(lines)
        if f"## {heading}" not in content:
            block = f"## {heading}\n{block}"
        self.append(rel, block)
        return lines

    def move(self, rel: str, new_rel: str, update_links: bool = True) -> dict:
        self.scan(force=True)
        new_rel = self.norm_note_path(new_rel)
        if rel not in self.notes:
            raise FileNotFoundError(rel)
        src, dst = self.safe_path(rel), self.safe_path(new_rel)
        if dst.exists() and not (os.path.normcase(str(src)) == os.path.normcase(str(dst))):
            raise FileExistsError(f"Уже существует: {new_rel}")
        stamp = self._stamp()
        updated = []
        if update_links:
            new_stem = new_rel.rsplit("/", 1)[-1][:-3]
            dup = [p for p in self._by_name.get(new_stem.lower(), []) if p != rel]
            new_link = new_rel[:-3] if dup else new_stem
            for p in sorted(set(self.in_edges.get(rel, ())) | {rel}):
                text = self.read(p)
                new_text = self._rewrite_links(text, p, rel, new_link)
                if new_text != text:
                    self._backup(p, stamp)
                    self._write(p, new_text)
                    updated.append(new_rel if p == rel else p)
        self._backup(rel, stamp)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        self.scan(force=True)
        return {"path": new_rel, "updated_links_in": updated}

    def _rewrite_links(self, text: str, src: str, old: str, new_link: str) -> str:
        def repl(m):
            embed, target, anchor, alias = m.group(1), m.group(2), m.group(3) or "", m.group(4)
            if not target.strip() or self.resolve(target, src) != old:
                return m.group(0)
            return f"{embed}[[{new_link}{anchor}{'|' + alias if alias is not None else ''}]]"
        return WIKILINK_RE.sub(repl, text)

    def trash(self, rel: str) -> str:
        """Moves a note into the vault's .trash folder (Obsidian's local trash)."""
        src = self.safe_path(rel)
        if not src.exists():
            raise FileNotFoundError(rel)
        trash_dir = self.root / ".trash"
        trash_dir.mkdir(exist_ok=True)
        dst = trash_dir / src.name
        i = 1
        while dst.exists():
            dst = trash_dir / f"{src.stem} ({i}){src.suffix}"
            i += 1
        os.replace(src, dst)
        self.scan(force=True)
        return dst.relative_to(self.root).as_posix()


ATTACHMENT_EXT = {
    "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "pdf", "mp3", "mp4", "wav", "ogg", "webm",
    "m4a", "mov", "canvas", "excalidraw", "zip", "docx", "xlsx", "pptx", "txt", "csv", "json",
}
