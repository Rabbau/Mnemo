"""Obsidian templates (core Templates / Templater plugin): discovery, usage hints and rendering."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from .vault import normalize_tag, split_frontmatter

RU_MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
RU_MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
RU_MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
RU_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
RU_WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

MOMENT_TOKEN_RE = re.compile(r"\[([^\]]*)\]|YYYY|YY|MMMM|MMM|MM|M|Do|DD|D|dddd|ddd|dd|HH|H|mm|ss")
TEMPLATER_TAG_RE = re.compile(r"<%([-_]?)(\*?)(.*?)([-_]?)%>", re.S)
HINT_COMMENT_RE = re.compile(r"/\*(.*?)\*/", re.S)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
DEFAULT_TEMPLATE_DIRS = ("template", "templates", "Templates", "Шаблоны", "шаблоны")


def moment_format(d: dt.datetime, fmt: str) -> str:
    """Subset of moment.js formatting (the one Templater's tp.date uses), Russian locale."""
    # moment's ru locale uses the genitive month after a day number: "26 сентября" but "сентябрь 2026"
    genitive = bool(re.search(r"D[oD]?(\[[^\[\]]*\]|\s)+MMMM", fmt))

    def rep(m):
        if m.group(1) is not None:
            return m.group(1)
        t = m.group(0)
        return {
            "YYYY": f"{d.year:04d}", "YY": f"{d.year % 100:02d}",
            "MMMM": (RU_MONTHS_GEN if genitive else RU_MONTHS)[d.month - 1],
            "MMM": RU_MONTHS_SHORT[d.month - 1], "MM": f"{d.month:02d}", "M": str(d.month),
            "Do": f"{d.day}-е", "DD": f"{d.day:02d}", "D": str(d.day),
            "dddd": RU_WEEKDAYS[d.weekday()], "ddd": RU_WEEKDAYS_SHORT[d.weekday()], "dd": RU_WEEKDAYS_SHORT[d.weekday()],
            "HH": f"{d.hour:02d}", "H": str(d.hour), "mm": f"{d.minute:02d}", "ss": f"{d.second:02d}",
        }[t]

    return MOMENT_TOKEN_RE.sub(rep, fmt)


def _str_args(code: str) -> list:
    """Literal arguments of a single call: strings, numbers, booleans."""
    inside = code[code.find("(") + 1: code.rfind(")")] if "(" in code else ""
    return [m.group(1) if m.group(1) is not None else m.group(2)
            for m in re.finditer(r"""["'](.*?)["']|(-?\d+|true|false)""", inside)]


class TemplateContext:
    def __init__(self, title: str, folder: str, now: dt.datetime | None = None):
        self.title, self.folder, self.now = title, folder, now or dt.datetime.now()
        self.warnings: list[str] = []

    def eval(self, code: str) -> str:
        code = code.strip()
        args = _str_args(code)
        fmt = args[0] if args and not re.fullmatch(r"-?\d+|true|false", args[0]) else None
        if code == "tp.file.title":
            return self.title
        if code.startswith("tp.date.now"):
            offset = next((int(a) for a in args[1:] if re.fullmatch(r"-?\d+", a)), 0)
            return moment_format(self.now + dt.timedelta(days=offset), fmt or "YYYY-MM-DD")
        if code.startswith("tp.date.tomorrow"):
            return moment_format(self.now + dt.timedelta(days=1), fmt or "YYYY-MM-DD")
        if code.startswith("tp.date.yesterday"):
            return moment_format(self.now - dt.timedelta(days=1), fmt or "YYYY-MM-DD")
        if code.startswith(("tp.file.creation_date", "tp.file.last_modified_date")):
            return moment_format(self.now, fmt or "YYYY-MM-DD HH:mm")
        if code.startswith("tp.file.folder"):
            return self.folder if "true" in args else self.folder.rsplit("/", 1)[-1]
        # core Templates plugin placeholders are handled separately; anything else is unsupported
        self.warnings.append(code)
        return ""


def render_text(text: str, ctx: TemplateContext) -> str:
    """Render Templater tags (<% expr %>, <%* code %>, whitespace control -/_) and core {{title}}/{{date}}."""
    out, pos, trim_next = [], 0, ""
    for m in TEMPLATER_TAG_RE.finditer(text):
        left, star, code, right = m.groups()
        chunk = _trim_start(text[pos:m.start()], trim_next)
        if left == "-":
            chunk = re.sub(r"\r?\n[ \t]*$", "", chunk)
        elif left == "_":
            chunk = chunk.rstrip()
        out.append(chunk)
        if not star:  # execution blocks (<%* %>) only hold comments/JS in these templates -> dropped
            out.append(ctx.eval(code))
        trim_next, pos = right, m.end()
    out.append(_trim_start(text[pos:], trim_next))
    result = "".join(out)

    def core(m):
        key, fmt = m.group(1).lower(), m.group(2)
        if key == "title":
            return ctx.title
        return moment_format(ctx.now, fmt or ("HH:mm" if key == "time" else "YYYY-MM-DD"))
    result = re.sub(r"\{\{\s*(title|date|time)(?::([^}]*))?\s*\}\}", core, result, flags=re.I)
    # a leading execution block leaves blank lines before the frontmatter
    if result.lstrip().startswith("---"):
        result = result.lstrip()
    return result


def _trim_start(chunk: str, mode: str) -> str:
    if mode == "-":
        return re.sub(r"^[ \t]*\r?\n", "", chunk, count=1)
    if mode == "_":
        return chunk.lstrip()
    return chunk


class Templates:
    def __init__(self, vault_root: Path):
        self.root = Path(vault_root)
        self.folder, self.folder_map = self._read_config()

    # ------------------------------------------------------------ config

    def _read_json(self, rel: str) -> dict:
        try:
            return json.loads((self.root / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _read_config(self):
        templater = self._read_json(".obsidian/plugins/templater-obsidian/data.json")
        core = self._read_json(".obsidian/templates.json")
        folder = (templater.get("templates_folder") or core.get("folder") or "").strip("/")
        if not folder or not (self.root / folder).is_dir():
            folder = next((d for d in DEFAULT_TEMPLATE_DIRS if (self.root / d).is_dir()), "")
        mapping = {}
        if templater.get("enable_folder_templates", True):
            for ft in templater.get("folder_templates") or []:
                f, t = (ft.get("folder") or "").strip("/"), (ft.get("template") or "").strip("/")
                if f and t:
                    mapping[f] = t
        return folder, mapping

    # ------------------------------------------------------------ listing

    def list(self) -> list:
        if not self.folder:
            return []
        base = self.root / self.folder
        by_template = {}
        for f, t in self.folder_map.items():
            by_template.setdefault(t.lower(), f)
        items = []
        for p in sorted(base.rglob("*.md")):
            rel = p.relative_to(self.root).as_posix()
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hint = self._hint(text)
            folder = by_template.get(rel.lower()) or by_template.get(rel[:-3].lower())
            if not folder:
                m = re.search(r"Клади в:\s*(.+)", hint)
                folder = m.group(1).strip().strip("/") if m else ""
            sections = [h for h in HEADING_RE.findall(re.sub(r"```.*?```", "", text, flags=re.S)) if "<%" not in h]
            fm, _, _ = split_frontmatter(render_text(text, TemplateContext(p.stem, folder or "")))
            raw_tags = fm.get("tags")
            tags = [normalize_tag(t) for t in (raw_tags if isinstance(raw_tags, list) else [raw_tags]) if t]
            items.append({
                "name": p.stem, "path": rel, "folder": folder, "hint": hint,
                "sections": sections, "tags": tags,
            })
        return items

    @staticmethod
    def _hint(text: str) -> str:
        block = next((b for b in TEMPLATER_TAG_RE.finditer(text) if b.group(2) == "*"), None)
        m = HINT_COMMENT_RE.search(block.group(3)) if block else None
        if not m:
            return ""
        lines = [ln.strip() for ln in m.group(1).splitlines()]
        hint = "\n".join(ln for ln in lines if ln and not ln.upper().startswith("КОГДА ИСПОЛЬЗОВАТЬ"))
        return hint.strip()

    def get(self, name: str) -> dict | None:
        if not name:
            return None
        key = name.strip().removesuffix(".md").lower()
        for t in self.list():
            if key in (t["name"].lower(), t["path"][:-3].lower()):
                return t
        return None

    def for_folder(self, folder: str) -> dict | None:
        """Template assigned to a folder (Templater picks the deepest configured parent)."""
        folder = (folder or "").strip("/")
        best = None
        for f, t in self.folder_map.items():
            if folder == f or folder.startswith(f + "/"):
                if not best or len(f) > len(best[0]):
                    best = (f, t)
        if best:
            return self.get(best[1])
        return next((t for t in self.list() if t["folder"] and t["folder"] == folder), None)

    def render(self, name: str, title: str, folder: str = "") -> tuple[str, list]:
        t = self.get(name)
        if not t:
            raise ValueError(f"Шаблон не найден: {name}")
        text = (self.root / t["path"]).read_text(encoding="utf-8", errors="replace")
        ctx = TemplateContext(title, folder)
        return render_text(text, ctx), ctx.warnings


def split_frontmatter_block(text: str) -> tuple[str, str]:
    m = re.match(r"\A(---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z))", text, re.S)
    return (m.group(1), text[m.end():]) if m else ("", text)


def _cut_runaway(text: str, skeleton: str) -> str:
    """Small models keep going after the note: a second note (new H1) or echoed context blocks."""
    lines, in_code, h1_seen = text.split("\n"), False, 0
    single_h1 = len(re.findall(r"^# \S", re.sub(r"```.*?```", "", skeleton, flags=re.S), re.M)) == 1
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if ln.startswith("### Заметка [[") or ln.strip() in ("--- ШАБЛОН ---", "--- КОНЕЦ ШАБЛОНА ---"):
            return _strip_trailing_rules("\n".join(lines[:i]))
        if single_h1 and re.match(r"# \S", ln):
            h1_seen += 1
            if h1_seen == 2:
                return _strip_trailing_rules("\n".join(lines[:i]))
    return text


def _strip_trailing_rules(text: str) -> str:
    return re.sub(r"(\s*\n---\s*)+$", "", text.rstrip()).rstrip()


H2_RE = re.compile(r"^##\s+(.+?)\s*$")
H1_RE = re.compile(r"^#\s+\S")


def _norm_words(heading: str) -> list:
    # emoji and punctuation dropped, words cut to a crude stem: "Примеры кода" ~ "Пример кода"
    return [w[:5] for w in re.findall(r"\w+", heading.lower())]


def _split_sections(body: str):
    """-> (preamble before the first H2, [(heading line, words, content)]); code blocks are respected."""
    pre, secs, cur, in_code = [], [], None, False
    for ln in body.split("\n"):
        if ln.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else H2_RE.match(ln)
        if m:
            cur = (ln, _norm_words(m.group(1)), [])
            secs.append(cur)
        else:
            (cur[2] if cur else pre).append(ln)
    return "\n".join(pre), [(h, w, "\n".join(c)) for h, w, c in secs]


def _clean_section(text: str) -> str:
    # drop echoed "### Заметка ..." blocks and trailing horizontal rules
    m = re.search(r"^#{1,6}\s*Заметк[аи]\b.*$", text, re.M)
    if m:
        text = text[:m.start()]
    return _strip_trailing_rules(text).strip("\n")


def _similar(a: list, b: list) -> bool:
    if a == b:
        return True
    sa, sb = set(a), set(b)
    return bool(sa and sb) and len(sa & sb) / len(sa | sb) >= 0.5


def assemble_from_skeleton(skeleton: str, filled: str) -> str | None:
    """Rebuild the note on the template's skeleton: template frontmatter, H1 and H2 sections in template order,
    content of each section taken from the model when it has a matching heading. Extra sections are dropped."""
    fm_tpl, sk_body = split_frontmatter_block(skeleton)
    sk_pre, sk_secs = _split_sections(sk_body)
    if not sk_secs:
        return None
    _, out_body = split_frontmatter_block(filled)
    out_pre, out_secs = _split_sections(out_body)

    def without_h1(text):
        return "\n".join(ln for ln in text.split("\n") if not H1_RE.match(ln)).strip("\n")

    sk_h1 = next((ln for ln in sk_pre.split("\n") if H1_RE.match(ln)), "")
    pre = _clean_section(without_h1(out_pre)) or without_h1(sk_pre)
    parts = [fm_tpl.rstrip("\r\n")] if fm_tpl else []
    if sk_h1:
        parts.append(("\n" if fm_tpl else "") + sk_h1)
    if pre.strip():
        parts.append("\n" + pre.strip("\n"))
    for heading, words, sk_content in sk_secs:
        best = ""
        for _, w, c in out_secs:
            c = _clean_section(c)
            if _similar(words, w) and len(c.strip()) > len(best.strip()):
                best = c
        content = best if best.strip() else sk_content.strip("\n")
        parts.append(f"\n{heading}\n{content}".rstrip())
    return "\n".join(parts).strip("\n") + "\n"


def unwrap_fence(text: str) -> str:
    """Remove a ```markdown ... ``` wrapper around the whole answer (but not a code block at the end)."""
    text = text.strip()
    m = re.match(r"```(?:markdown|md)?[ \t]*\n(.*)\n```$", text, re.S)
    return m.group(1).strip() if m else text


def merge_filled(skeleton: str, filled: str) -> str:
    """Model output for a template: keep the template's frontmatter (correct dates/tags), take the model's body."""
    filled = _cut_runaway(unwrap_fence(filled), skeleton)
    if skeleton:
        assembled = assemble_from_skeleton(skeleton, filled)
        if assembled:
            return assembled
    fm_tpl, _ = split_frontmatter_block(skeleton)
    fm_out, body = split_frontmatter_block(filled)
    if not fm_tpl:
        return filled + "\n"
    if fm_out and "<%" not in fm_out and len(fm_out) >= len(fm_tpl) * 0.6:
        # model kept (and maybe extended) the frontmatter — fine, unless it dropped keys; keep template keys
        tpl_keys = set(re.findall(r"^([^\s:#-][^:\n]*):", fm_tpl, re.M))
        out_keys = set(re.findall(r"^([^\s:#-][^:\n]*):", fm_out, re.M))
        if tpl_keys <= out_keys:
            return filled + "\n"
    return fm_tpl + body.lstrip("\r\n") + "\n"
